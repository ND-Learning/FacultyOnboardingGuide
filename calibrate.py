#!/usr/bin/env python3
"""
calibrate.py -- deterministic calibration of the ODL effort estimator from
data_all/ (real Asana time entries + task timestamps). NO GUESSING:
every emitted number is computed from rows in these files and carries
provenance (source file, filter, n). Where the data cannot support a number,
the block says so explicitly instead of inventing one.

Inputs (relative to --dir, default data_all/):
  time_entries.csv              2,335 real time-tracking entries (ground truth)
  derived/audit_coverage.csv    per-project logging-coverage audit (full/partial/minimal)
  derived/ground_truth.csv      25-project calibration set (full coverage, course-dev, >=10h)
  derived/archetypes.csv        archetype + deliverable counts per project
  derived/calendar_spans.csv    per-project completed_at spans + reliability flags
  derived/phase_spans.csv       per-(project,phase) calendar spans
  ../odl_media_unit_rates.csv   ODL's own per-deliverable estimation board (prior)

Outputs (in --dir/derived/):
  calibration.json              every calibrated block, with provenance
  charts/*.png                  the evidence, drawn
  (stdout)                      human-readable readout incl. backtest

Method notes (decided from the audit, all documented in provenance fields):
  * PM time is bimodal: only projects with a dedicated "PM Time Tracking"
    section log PM at all. So PM is modeled ONLY from those projects, as
    hours-per-calendar-week, and production effort is modeled from non-PM
    hours of projects that actually logged production work. A flat global
    PM% would be an artifact of who used a PM section.
  * Entry phases are re-derived here with ordered keyword rules applied to
    the section name, falling back to the task name when the section is
    uninformative ("Untitled section", per-video sections like "Video 2 - X",
    slash-compound names like "Post-Production / Design Development"). This
    re-bins the 109h of "Untitled section" time and ~26h of course-build work
    that the compound section name forced into Post-Production.
  * Blending with the ODL unit-rate board uses shrinkage w = n/(n+3)
    (README design): rate = w*observed + (1-w)*board_prior.
"""
import argparse, csv, json, os, re, statistics as st
from collections import defaultdict

# --------------------------------------------------------------------------- #
# Phase mapping: ordered keyword rules. First match wins. Applied to the
# section name; if the section is uninformative, applied to the task name.
# --------------------------------------------------------------------------- #
PM_KEYS = ("pm time", "pm tracking")
LD_KEYS = ("ld time",)
PHASE_RULES = [
    (("post-project", "post project", "evaluation", "retro", "reflection",
      "survey", "handoff", "delivery"), "Evaluation"),
    (("post-production", "post prod", "post-prod", "edit", "final cut",
      "trailer", "fc1", "rough cut", "color grade"), "Post-Production"),
    (("pre-production", "pre prod", "pre-prod", "script", "storyboard",
      "schedule", "filming details", "production details", "prep"), "Pre-Production"),
    (("design development", "course build", "build", "develop", "assessment",
      "lms", "canvas"), "Development/Build"),
    (("production", "film", "shoot", "record", "studio", "b-roll",
      "capture"), "Production"),
    (("qa", "launch", "quality"), "QA & Launch"),
    (("design", "course map", "objectives"), "Design"),
    (("analysis", "intake", "kickoff", "charter", "discovery",
      "planning"), "Discovery"),
    (("media", "video", "animation", "graphic"), "Production"),
]
VIDEO_SECTION_RE = re.compile(r"^\s*(video|episode)\s*\d+", re.I)


def keyword_phase(text):
    s = (text or "").lower()
    for keys, ph in PHASE_RULES:
        if any(k in s for k in keys):
            return ph
    return None


def classify_entry(section, task_name):
    """-> (phase, basis) where basis records which field decided it."""
    s = (section or "").strip().lower()
    if any(k in s for k in PM_KEYS):
        return "PM (cross-phase)", "section"
    if any(k in s for k in LD_KEYS):
        return "Learning Design (cross-phase)", "section"
    uninformative = (s in ("", "untitled section", "(no section)")
                     or VIDEO_SECTION_RE.match(section or "")
                     # slash-compound sections ("Post-Production / Design
                     # Development") are ambiguous -- let the task name decide
                     or " / " in s)
    if not uninformative:
        ph = keyword_phase(section)
        if ph:
            return ph, "section"
    ph = keyword_phase(task_name)
    if ph:
        return ph, "task_name"
    ph = keyword_phase(section)  # last resort: informative-ish section anyway
    if ph:
        return ph, "section"
    return "Other / unsorted", "none"


def quart(values):
    """P25/P50/P75/P80 of a list. Interpolated percentiles need n>=4 --
    for n of 2-3 they would be illusory precision, so raw values + median
    are reported instead; n<2 -> None."""
    v = sorted(values)
    if len(v) < 2:
        return None
    if len(v) < 4:
        return {"n": len(v), "values": [round(x, 1) for x in v],
                "p50": round(st.median(v), 1),
                "note": "n<4 -- raw values shown; interpolated quartiles withheld"}
    qs = st.quantiles(v, n=20, method="inclusive")  # 5%,10%,...,95%
    return {"n": len(v), "min": round(v[0], 1), "p25": round(qs[4], 1),
            "p50": round(st.median(v), 1), "p75": round(qs[14], 1),
            "p80": round(qs[15], 1), "max": round(v[-1], 1)}


def shrink(observed, prior, n, k=3.0):
    """README shrinkage blend: w = n/(n+k)."""
    w = n / (n + k)
    return round(w * observed + (1 - w) * prior, 1), round(w, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(__file__), "data_all"))
    args = ap.parse_args()
    D = args.dir
    DV = os.path.join(D, "derived")
    os.makedirs(os.path.join(DV, "charts"), exist_ok=True)

    rd = lambda p: list(csv.DictReader(open(p, newline="")))
    entries = rd(os.path.join(D, "time_entries.csv"))
    coverage = {r["project_gid"]: r for r in rd(os.path.join(DV, "audit_coverage.csv"))}
    ground = {r["gid"]: r for r in rd(os.path.join(DV, "ground_truth.csv"))}
    arch = {r["gid"]: r for r in rd(os.path.join(DV, "archetypes.csv"))}
    spans = {r["project_gid"]: r for r in rd(os.path.join(DV, "calendar_spans.csv"))}
    board = rd(os.path.join(os.path.dirname(D.rstrip("/")), "odl_media_unit_rates.csv"))

    # ---- 1. re-phase every entry; build per-project matrices --------------- #
    proj = defaultdict(lambda: defaultdict(float))   # gid -> phase -> hours
    proj_name, basis_count = {}, defaultdict(int)
    video_hours = defaultdict(lambda: defaultdict(float))  # gid -> video sec -> h
    video_phase = defaultdict(lambda: defaultdict(float))  # gid -> phase -> h (video sections only)
    for e in entries:
        gid = e["project_gid"]
        proj_name[gid] = e["project_name"].strip()
        h = float(e["hours"] or 0)
        ph, basis = classify_entry(e["section"], e["task_name"])
        proj[gid][ph] += h
        basis_count[basis] += 1
        if VIDEO_SECTION_RE.match(e["section"] or ""):
            video_hours[gid][e["section"].strip()] += h
            if ph in ("Pre-Production", "Production", "Post-Production"):
                video_phase[gid][ph] += h

    total_all = round(sum(sum(p.values()) for p in proj.values()), 1)
    pm_of = lambda gid: proj[gid].get("PM (cross-phase)", 0.0)
    nonpm_of = lambda gid: sum(v for k, v in proj[gid].items() if k != "PM (cross-phase)")

    # ---- 2. selection sets (rules stated, applied mechanically) ------------ #
    # production-effort set: ground-truth (full coverage, course-dev, >=10h),
    # archetype != other (drops the NEON MOU board), and actually logged
    # production work (non-PM >= 10h) -- PM-container boards can't witness
    # production effort they never logged.
    prod_set = [g for g in ground
                if ground[g]["archetype"] != "other" and nonpm_of(g) >= 10.0]
    # PM set: any HUMAN-REVIEWED project with a PM section (pm hours > 0) and a
    # reliable span. Unreviewed/excluded projects must not move ANY calibrated
    # number -- without this gate a brand-new board with a PM Time Tracking
    # section would silently enter the PM model before anyone vetted it.
    reviewed = lambda g: (coverage.get(g, {}).get("coverage", "unreviewed")
                          not in ("unreviewed", "excluded"))
    pm_set = [g for g in proj if pm_of(g) > 0 and reviewed(g)
              and spans.get(g, {}).get("reliable") == "True"
              and float(spans[g]["span_weeks"]) >= 1.0]

    # ---- 3. block A: production effort by archetype ------------------------ #
    by_arch = defaultdict(list)
    for g in prod_set:
        a = ground[g]["archetype"]
        nv = arch.get(g, {}).get("n_videos")
        if a == "single_video" and nv and float(nv) > 1:
            a = "video_series"  # n_videos from task names overrides the label
        by_arch[a].append((proj_name[g], round(nonpm_of(g), 1)))
    archetype_effort = {}
    for a, rows in sorted(by_arch.items()):
        archetype_effort[a] = {
            "projects": dict(rows),
            "production_hours": quart([h for _, h in rows]) or
                {"n": len(rows), "values": [h for _, h in rows],
                 "note": "n<2 -- range not computable, value(s) shown raw"},
        }

    # ---- 4. block B: PM model (hours per calendar week) -------------------- #
    pm_rows = []
    for g in pm_set:
        wk = float(spans[g]["span_weeks"])
        pm_rows.append({"project": proj_name[g], "pm_hours": round(pm_of(g), 1),
                        "span_weeks": round(wk, 1),
                        "pm_per_week": round(pm_of(g) / wk, 2)})
    pm_rows.sort(key=lambda r: -r["pm_hours"])
    pm_share_rows = [
        {"project": proj_name[g], "pm_share_pct":
         round(100 * pm_of(g) / (pm_of(g) + nonpm_of(g)), 1)}
        for g in pm_set if nonpm_of(g) >= 10.0]
    pm_model = {
        "method": "PM modeled ONLY from projects with a dedicated PM Time Tracking "
                  "section AND a reliable calendar span. pm_per_week = pm_hours / span_weeks. "
                  "Global 33% PM share is an artifact (10 boards log PM, the rest log 0%) -- do not use.",
        "projects": pm_rows,
        "pm_hours_per_week": quart([r["pm_per_week"] for r in pm_rows]),
        "pm_share_where_both_logged": pm_share_rows,
        "pm_share_pct": quart([r["pm_share_pct"] for r in pm_share_rows]),
    }

    # ---- 5. block C: per-video unit rates ---------------------------------- #
    # unit = one named video with full-coverage logging. Sources: per-video
    # sections (video_hours) in full-coverage projects + single-video projects
    # in the ground-truth set.
    video_obs, video_obs_partial = [], []
    for g, vids in video_hours.items():
        cov = coverage.get(g, {}).get("coverage")
        for sec, h in vids.items():
            row = {"project": proj_name[g], "video": sec, "hours": round(h, 1)}
            (video_obs if cov == "full" else video_obs_partial).append(row)
    for g in ground:  # single-video projects: whole project = one video
        a = arch.get(g, {})
        if ground[g]["archetype"] == "single_video" and a.get("n_videos") == "1" \
           and g not in video_hours:  # skip if already counted via its video section
            video_obs.append({"project": proj_name[g], "video": "(whole project)",
                              "hours": round(nonpm_of(g), 1)})
    # multi-video full-coverage projects without per-video sections: per-video average
    for g in ground:
        nv = arch.get(g, {}).get("n_videos")
        if nv and float(nv) > 1 and g not in video_hours and \
           ground[g]["archetype"] in ("video_series", "single_video"):
            video_obs.append({"project": proj_name[g],
                              "video": f"(avg of {int(float(nv))} videos)",
                              "hours": round(nonpm_of(g) / float(nv), 1),
                              "is_average": True})
    obs_vals = [r["hours"] for r in video_obs]
    obs_q = quart(obs_vals)
    # ODL board prior: per-1-video full-lifecycle rates for comparable types
    board_video = {r["deliverable"]: float(r["est_hours"]) for r in board
                   if "per 1 video" in r["section"]}
    lecture_like = [v for k, v in board_video.items()
                    if any(t in k.lower() for t in ("lecture", "lightboard", "screencast"))]
    prior = st.median(lecture_like)
    blended, w = shrink(obs_q["p50"], prior, obs_q["n"]) if obs_q else (None, None)
    video_rates = {
        "unit": "hours per produced short video, full lifecycle (pre+prod+post), ODL staff time",
        "observed": video_obs,
        "observed_quartiles": obs_q,
        "observed_excluded_partial_coverage": video_obs_partial,
        "board_prior_per_video": board_video,
        "board_prior_used": {"value": prior, "basis":
            "median of board lecture-like per-1-video rates (lecture/lightboard/screencast)"},
        "blended_generic_video_p50": {"value": blended, "shrinkage_w": w,
            "formula": "w*observed_p50 + (1-w)*board_prior, w = n/(n+3)"},
        "not_calibratable": "interview/testimonial (board: 34-41h) and mini-doc (70h) "
            "rates have NO full-coverage logged exemplar yet -- board values stand, unvalidated.",
    }

    # ---- 6. block D: media lifecycle split (pre/prod/post) ----------------- #
    pool, pool_projects = defaultdict(float), set()
    for g, phs in video_phase.items():
        if coverage.get(g, {}).get("coverage") == "full":
            pool_projects.add(proj_name[g])
            for ph, h in phs.items():
                pool[ph] += h
    tot = sum(pool.values())
    media_split = {
        "basis": f"pooled hours on per-video sections with full-coverage logging: "
                 f"{len(pool_projects)} project(s) ({', '.join(sorted(pool_projects))}), "
                 f"{round(tot,1)}h total",
        "low_confidence": len(pool_projects) < 3,
        "warning": ("SINGLE exemplar -- one project's one video; not a generic "
                    "lifecycle split yet" if len(pool_projects) < 2 else None),
        "split_pct": {ph: round(100 * h / tot, 1) for ph, h in sorted(pool.items())} if tot else None,
    }

    # ---- 7. block E: full-course phase mix (non-PM) ------------------------ #
    mix_pool, mix_n = defaultdict(float), 0
    for g in prod_set:
        if ground[g]["archetype"] == "full_course":
            mix_n += 1
            for ph, h in proj[g].items():
                if ph != "PM (cross-phase)":
                    mix_pool[ph] += h
    mt = sum(mix_pool.values())
    course_phase_mix = {
        "basis": f"pooled non-PM hours of {mix_n} full-coverage full_course projects ({round(mt,1)}h)",
        "share_pct": {ph: round(100 * h / mt, 1)
                      for ph, h in sorted(mix_pool.items(), key=lambda kv: -kv[1])},
    }

    # ---- 8. block F: calendar durations ------------------------------------ #
    cal_by_arch = defaultdict(list)
    for g, r in spans.items():
        if r["reliable"] != "True":
            continue
        a = arch.get(g, {}).get("archetype") or (ground.get(g, {}) or {}).get("archetype")
        if a and a not in ("internal_admin", "other"):
            cal_by_arch[a].append(float(r["span_weeks"]))
    calendar = {
        "method": "weeks between first and last completed_at over completed tasks; "
                  "only spans with n_completed>=5, bulk_completion_frac<=0.5, span>0 "
                  "(bulk check-offs faked 0-day spans on ~34 boards -- excluded).",
        "span_weeks_by_archetype": {a: quart(v) or {"n": len(v), "values": [round(x,1) for x in v]}
                                    for a, v in sorted(cal_by_arch.items())},
    }
    # per-phase calendar medians from phase_spans.csv (reliable projects only)
    ph_spans = defaultdict(list)
    for r in rd(os.path.join(DV, "phase_spans.csv")):
        if r["project_reliable"] == "True" and float(r["bulk_completion_frac"]) <= 0.5:
            ph_spans[r["phase"]].append(float(r["span_days"]))
    calendar["phase_span_days"] = {ph: quart(v) for ph, v in sorted(ph_spans.items()) if quart(v)}

    # ---- 9. block G: explicitly NOT calibratable ---------------------------- #
    not_calibratable = {
        "faculty_time": "time_entries contains ODL STAFF time only -- zero faculty "
                        "hours logged. All faculty-time figures remain planning "
                        "estimates and must be labeled as such.",
        "discovery_effort": f"only {round(sum(p.get('Discovery',0) for p in proj.values()),1)}h "
                            "logged across all projects -- discovery happens before boards "
                            "get tracked. Insufficient.",
        "evaluation_effort": f"only {round(sum(p.get('Evaluation',0) for p in proj.values()),1)}h "
                             "logged total. Insufficient.",
        "qa_effort": f"only {round(sum(p.get('QA & Launch',0) for p in proj.values()),1)}h logged "
                     "total. Insufficient.",
        "interview_minidoc_video_rates": "no full-coverage logged exemplar (see video block).",
        "xr_effort": "XR projects in data have <=6h logged (Equity XR 6h, XR Spanish 1h) -- "
                     "board rates (40-80h) stand, unvalidated.",
        "per_module_rate": "module counts exist for only 5 of 19 course projects and mean "
                           "different things (AI for ND 57h/module vs R&E AI 7.6h/week-unit "
                           "vs Virtual Borders 25h/module) -- logging is not at module "
                           "granularity yet. Effort stays archetype-level until the new "
                           "per-task tracking accumulates.",
    }

    # ---- 10. backtest: leave-one-out --------------------------------------- #
    fc = sorted(h for _, h in by_arch.get("full_course", []))
    loo = []
    for i, v in enumerate(fc):
        rest = fc[:i] + fc[i + 1:]
        pred = st.median(rest)
        qs = st.quantiles(rest, n=4, method="inclusive")
        loo.append({"actual": v, "pred_p50": round(pred, 1),
                    "in_iqr": qs[0] <= v <= qs[2],
                    "ape_pct": round(100 * abs(v - pred) / v, 1)})
    backtest = {
        "method": "leave-one-out on full_course production hours: predict P50 of the "
                  "remaining projects, check if actual falls in their IQR.",
        "rows": loo,
        "median_ape_pct": round(st.median(r["ape_pct"] for r in loo), 1) if loo else None,
        "iqr_coverage_pct": round(100 * sum(r["in_iqr"] for r in loo) / len(loo), 1) if loo else None,
        "reading": "wide APE is expected -- it is the honest spread of a point guess; "
                   "this is WHY the estimator must quote P25-P80 ranges, not points.",
    }

    # ---- 11. assemble + write ----------------------------------------------- #
    entry_dates = sorted(e["entry_date"] for e in entries if e["entry_date"])
    cal = {
        "_provenance": {
            "source": "data_all/time_entries.csv ({:,} entries, {}h, {}..{}, "
                      "ODL staff time)".format(len(entries), total_all,
                                               entry_dates[0], entry_dates[-1]),
            "calibration_set": f"{len(ground)} full-coverage course-dev projects >=10h "
                               f"(derived/ground_truth.csv); production rates from the "
                               f"{len(prod_set)} of them with >=10h non-PM logging",
            "phase_mapping": f"ordered keyword rules on section, task-name fallback for "
                             f"uninformative sections; basis counts: {dict(basis_count)}",
            "no_guessing": "blocks state 'not_calibratable' where data is insufficient",
        },
        "archetype_effort_hours": archetype_effort,
        "pm_model": pm_model,
        "video_unit_rates": video_rates,
        "media_lifecycle_split": media_split,
        "full_course_phase_mix": course_phase_mix,
        "calendar": calendar,
        "not_calibratable": not_calibratable,
        "backtest": backtest,
    }
    out = os.path.join(DV, "calibration.json")
    json.dump(cal, open(out, "w"), indent=2, ensure_ascii=False)
    print("wrote", out)

    # ---- 12. charts ---------------------------------------------------------- #
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    C = os.path.join(DV, "charts")

    # chart 1: per-project hours, PM vs production, calibration set
    gt_sorted = sorted(ground, key=lambda g: pm_of(g) + nonpm_of(g))
    names = [proj_name[g][:30] + ("…" if len(proj_name[g]) > 30 else "")
             for g in gt_sorted]
    pmv = [pm_of(g) for g in gt_sorted]
    npv = [nonpm_of(g) for g in gt_sorted]
    fig, ax = plt.subplots(figsize=(11.5, 8))
    ax.barh(names, npv, label="production (non-PM)", color="#2a9d8f")
    ax.barh(names, pmv, left=npv, label="PM", color="#e9c46a")
    ax.set_title("Calibration set: logged hours per project (PM vs production)")
    ax.set_xlabel("hours"); ax.legend(); fig.tight_layout()
    fig.savefig(os.path.join(C, "1_project_hours.png"), dpi=140); plt.close(fig)

    # chart 2: full-course production-hours distribution
    if fc:
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.scatter(fc, [1] * len(fc), s=60, zorder=3, color="#264653")
        q = archetype_effort["full_course"]["production_hours"]
        for k, c in (("p25", "#2a9d8f"), ("p50", "#e76f51"), ("p80", "#e9c46a")):
            ax.axvline(q[k], color=c, ls="--", label=f"{k.upper()} = {q[k]}h")
        ax.set_yticks([]); ax.legend()
        ax.set_title(f"Full-course production hours, n={q['n']} (each dot = one project)")
        ax.set_xlabel("non-PM hours"); fig.tight_layout()
        fig.savefig(os.path.join(C, "2_full_course_hours.png"), dpi=140); plt.close(fig)

    # chart 3: observed per-video hours vs ODL board rates
    fig, ax = plt.subplots(figsize=(9, 5))
    ov = sorted(video_obs, key=lambda r: r["hours"])
    ax.barh([f"{r['project'][:24]} | {r['video'][:22]}" for r in ov],
            [r["hours"] for r in ov], color="#2a9d8f", label="observed (full coverage)")
    by_val = defaultdict(list)  # merge identical board rates into one label
    for k, v in board_video.items():
        if v <= 50:
            by_val[v].append(k.split("(")[0].strip()[:18])
    for v, ks in sorted(by_val.items()):
        ax.axvline(v, color="#999", ls=":", lw=1)
        ax.text(v, len(ov) - .3, " / ".join(ks), rotation=90,
                fontsize=6.5, va="top", color="#555")
    if blended:
        ax.axvline(blended, color="#e76f51", lw=2,
                   label=f"blended generic rate = {blended}h (w={w})")
    ax.set_title("Hours per video: observed vs ODL board rates (dotted)")
    ax.set_xlabel("hours"); ax.legend(); fig.tight_layout()
    fig.savefig(os.path.join(C, "3_video_rates.png"), dpi=140); plt.close(fig)

    # chart 4: PM hours vs calendar weeks
    fig, ax = plt.subplots(figsize=(8, 5))
    xs = [r["span_weeks"] for r in pm_rows]; ys = [r["pm_hours"] for r in pm_rows]
    ax.scatter(xs, ys, s=55, color="#264653")
    # annotate only the clearly-separated points; the dense low-PM cluster
    # stays unlabeled (labels overlapped illegibly -- data is in the JSON)
    for i, r in enumerate(pm_rows):
        if r["pm_hours"] >= 20 or r["span_weeks"] >= 45:
            ax.annotate(r["project"][:18], (r["span_weeks"], r["pm_hours"]),
                        fontsize=6.5, xytext=(4, 3), textcoords="offset points")
    q = pm_model["pm_hours_per_week"]
    import numpy as np
    xx = np.linspace(0, max(xs) * 1.05, 50)
    for k, c in (("p25", "#2a9d8f"), ("p50", "#e76f51"), ("p75", "#e9c46a")):
        ax.plot(xx, q[k] * xx, ls="--", color=c, label=f"{k.upper()}: {q[k]} h/wk")
    ax.set_title(f"PM hours vs project calendar length (n={q['n']} PM-tracked projects)")
    ax.set_xlabel("calendar weeks"); ax.set_ylabel("PM hours"); ax.legend()
    fig.tight_layout(); fig.savefig(os.path.join(C, "4_pm_model.png"), dpi=140); plt.close(fig)

    # chart 5: calendar span by archetype
    fig, ax = plt.subplots(figsize=(8, 4.5))
    labels, data = zip(*[(a, v) for a, v in sorted(cal_by_arch.items()) if len(v) >= 2])
    ax.boxplot(data, tick_labels=[f"{a}\n(n={len(v)})" for a, v in zip(labels, data)],
               vert=True, whis=(10, 90))
    ax.set_ylabel("calendar weeks"); ax.set_title("Project calendar span by archetype (reliable spans only)")
    fig.tight_layout(); fig.savefig(os.path.join(C, "5_calendar.png"), dpi=140); plt.close(fig)

    # chart 6: full-course phase mix
    fig, ax = plt.subplots(figsize=(8, 4.5))
    items = sorted(course_phase_mix["share_pct"].items(), key=lambda kv: -kv[1])
    ax.bar([k.replace(" ", "\n") for k, _ in items], [v for _, v in items], color="#2a9d8f")
    ax.set_ylabel("% of non-PM hours")
    ax.set_title(course_phase_mix["basis"])
    fig.tight_layout(); fig.savefig(os.path.join(C, "6_phase_mix.png"), dpi=140); plt.close(fig)
    print("wrote 6 charts to", C)

    # ---- 13. readable summary ------------------------------------------------ #
    print("\n=== CALIBRATION READOUT ===")
    print(f"production set (n={len(prod_set)}):", [proj_name[g] for g in prod_set])
    for a, blk in archetype_effort.items():
        print(f"  {a}: {blk['production_hours']}")
    print("PM h/wk:", pm_model["pm_hours_per_week"])
    print("PM share (both logged):", pm_model["pm_share_pct"])
    print("video p50 observed:", obs_q, "-> blended", blended, f"(w={w})")
    print("media split:", media_split["split_pct"])
    print("course mix:", course_phase_mix["share_pct"])
    print("backtest:", {k: backtest[k] for k in ("median_ape_pct", "iqr_coverage_pct")})


if __name__ == "__main__":
    main()
