#!/usr/bin/env python3
"""
audit_rules.py -- deterministic, re-runnable replacement for the one-off
multi-agent audit. Regenerates everything calibrate.py consumes:

  derived/audit_coverage.csv    per-project logging-coverage signals + label
  derived/archetypes.csv        archetype/deliverable labels (from the registry)
  derived/calendar_spans.csv    per-project completed_at spans + reliability
  derived/phase_spans.csv       per-(project,phase) calendar spans
  derived/ground_truth.csv      the calibration set
  derived/needs_review.csv      human queue: new/drifted projects (NO GUESSING)

Design (sustainability without guessing):
  * Hours/spans/signals are ALWAYS recomputed fresh from the latest pull --
    new time entries on known projects flow into the calibration automatically.
  * Judgment labels (archetype, is_course_dev, audited coverage) come from
    project_registry.csv. They were verified once by the 2026-06-04 audit and
    are FROZEN; the nightly job never re-invents them.
  * A NEW project with logged time but no registry row is EXCLUDED from the
    calibration set and queued in needs_review.csv (someone adds one registry
    row to admit it -- a 2-minute job).
  * DRIFT DETECTION: a frozen "full" project whose logging stops while task
    activity continues is demoted to "partial" (excluded) and queued for
    review. A human can re-admit it with force_coverage=full in the registry.

Usage:  python3 audit_rules.py --dir data_all [--registry project_registry.csv]
"""
import argparse, csv, os, re, statistics as st
from collections import defaultdict
from datetime import datetime, timedelta, timezone

TRACKING_START = datetime(2024, 8, 19, tzinfo=timezone.utc)
DRIFT_GAP_DAYS = 60   # full-coverage project: logging silent this long while
                      # tasks keep completing => coverage drift

# section -> canonical phase for CALENDAR spans (same ordered keyword spec the
# audited phase_spans.csv was built with; first match wins, case-insensitive)
CAL_PHASE_RULES = [
    (("pm time", "pm tracking"), None),  # cross-phase -> skip
    (("post-project", "post project", "evaluation", "retro", "reflection",
      "survey", "handoff", "delivery"), "Evaluation"),
    (("post-production", "post prod", "post-prod", "editing", "edit"), "Post-Production"),
    (("pre-production", "pre prod", "pre-prod", "script", "storyboard"), "Pre-Production"),
    (("design development", "course build", "build", "develop", "assessment",
      "lms", "canvas"), "Development/Build"),
    (("production", "film", "shoot", "record", "studio"), "Production"),
    (("media", "video", "animation", "graphic"), "Production"),
    (("qa", "launch", "quality"), "QA & Launch"),
    (("design", "course map", "map", "content", "objectives"), "Design"),
    (("analysis", "intake", "kickoff", "charter", "discovery", "planning"), "Discovery"),
]


def cal_phase(section):
    s = (section or "").lower()
    for keys, ph in CAL_PHASE_RULES:
        if any(k in s for k in keys):
            return ph
    return "Other"


def dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def norm_bool(s):
    """Robust to human registry edits: True/TRUE/true/yes/1 all count."""
    return (s or "").strip().lower() in ("true", "yes", "1")


def norm_label(s):
    return (s or "").strip().lower()


def bulk_frac(times):
    """max share of completions landing in the same minute."""
    if not times:
        return 0.0
    buckets = defaultdict(int)
    for d in times:
        buckets[d.replace(second=0, microsecond=0)] += 1
    return round(max(buckets.values()) / len(times), 4)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data_all"))
    ap.add_argument("--registry", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "project_registry.csv"))
    args = ap.parse_args()
    D, DV = args.dir, os.path.join(args.dir, "derived")
    os.makedirs(DV, exist_ok=True)

    rd = lambda p: list(csv.DictReader(open(p, newline="")))
    entries = rd(os.path.join(D, "time_entries.csv"))
    tasks = rd(os.path.join(D, "tasks_raw.csv"))
    registry = {r["gid"]: r for r in rd(args.registry)} if os.path.exists(args.registry) else {}
    if not registry:
        print("WARN: no project_registry.csv -- every project will queue for review")

    def write(name, rows, cols):
        path = os.path.join(DV, name)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {path} ({len(rows)} rows)")

    # ---- per-project signals from time_entries -------------------------------- #
    ent = defaultdict(lambda: {"hours": 0.0, "n": 0, "dates": [], "authors": set()})
    pname = {}
    for e in entries:
        g = e["project_gid"]
        pname[g] = e["project_name"].strip()
        ent[g]["hours"] += float(e["hours"] or 0)
        ent[g]["n"] += 1
        if e["entry_date"]:
            ent[g]["dates"].append(e["entry_date"])
        ent[g]["authors"].add(e["entry_author"])

    # ---- per-project signals from tasks_raw ----------------------------------- #
    tk = defaultdict(lambda: {"n_tasks": 0, "n_completed": 0, "completed_at": [],
                              "by_phase": defaultdict(list)})
    for t in tasks:
        g = t["project_gid"]
        pname.setdefault(g, t["project_name"].strip())
        tk[g]["n_tasks"] += 1
        if (t.get("completed") or "").lower() == "true":
            d = dt(t.get("completed_at"))
            if d:
                tk[g]["n_completed"] += 1
                tk[g]["completed_at"].append(d)
                ph = cal_phase(t.get("section"))
                if ph:
                    tk[g]["by_phase"][ph].append(d)

    # ---- calendar_spans.csv + phase_spans.csv (pure math) ---------------------- #
    cal_rows, phase_rows = [], []
    for g, a in sorted(tk.items(), key=lambda kv: -len(kv[1]["completed_at"])):
        ca = sorted(a["completed_at"])
        if not ca:
            continue
        span = (ca[-1] - ca[0]).total_seconds() / 86400.0
        bf = bulk_frac(ca)
        reliable = len(ca) >= 5 and bf <= 0.5 and span > 0
        cal_rows.append({
            "project_gid": g, "name": pname.get(g, ""),
            "n_completed": len(ca), "span_days": round(span, 3),
            "span_weeks": round(span / 7.0, 3), "bulk_completion_frac": round(bf, 4),
            "first_completed": ca[0].isoformat(), "last_completed": ca[-1].isoformat(),
            "reliable": reliable,
        })
        for ph, times in sorted(a["by_phase"].items()):
            ts = sorted(times)
            if len(ts) >= 3 and bulk_frac(ts) <= 0.5:
                phase_rows.append({
                    "project_gid": g, "name": pname.get(g, ""), "phase": ph,
                    "n_completed": len(ts),
                    "span_days": round((ts[-1] - ts[0]).total_seconds() / 86400.0, 3),
                    "bulk_completion_frac": round(bulk_frac(ts), 4),
                    "project_reliable": reliable,
                    "first_completed": ts[0].isoformat(), "last_completed": ts[-1].isoformat(),
                })
    write("calendar_spans.csv", cal_rows, list(cal_rows[0].keys()))
    write("phase_spans.csv", phase_rows, list(phase_rows[0].keys()))
    cal_by_gid = {r["project_gid"]: r for r in cal_rows}

    # ---- audit_coverage.csv: signals fresh, labels from registry --------------- #
    cov_rows, review = [], []
    for g, s in sorted(ent.items(), key=lambda kv: -kv[1]["hours"]):
        dates = sorted(s["dates"])
        ca = sorted(tk[g]["completed_at"]) if g in tk else []
        pct_in = (sum(1 for d in ca if d >= TRACKING_START) / len(ca)) if ca else 1.0
        reg = registry.get(g)
        drift = ""
        if reg:
            force = norm_label(reg.get("force_coverage"))
            label = force or norm_label(reg["coverage_audited"])
            is_cd = norm_bool(reg["is_course_dev"])
            # drift detection only matters for projects we'd include, and only
            # on evidence NEWER than what the audit already reviewed
            if label == "full" and not force and dates and ca:
                last_entry = datetime.fromisoformat(dates[-1]).replace(tzinfo=timezone.utc)
                audited_through = dt((reg.get("audited_through") or "1970-01-01").strip()
                                     + "T00:00:00+00:00")
                tail = [d for d in ca
                        if d > last_entry + timedelta(days=DRIFT_GAP_DAYS)
                        and d > audited_through]
                if tail:
                    drift = (f"coverage drift: {len(tail)} task completion(s) more than "
                             f"{DRIFT_GAP_DAYS}d after last time entry ({dates[-1]}) -- demoted "
                             "to partial; set force_coverage=full in the registry to re-admit")
                    label = "partial"
                    review.append({"gid": g, "name": pname.get(g, ""), "reason": drift,
                                   "logged_hours": round(s["hours"], 2)})
            if label == "exclude":
                # human ban: out of EVERY calibrated block (incl. the PM model)
                label, is_cd = "excluded", False
        else:
            label, is_cd = "unreviewed", False
            review.append({"gid": g, "name": pname.get(g, ""),
                           "reason": "NEW project with logged time but no registry row -- "
                                     "add archetype + is_course_dev to project_registry.csv",
                           "logged_hours": round(s["hours"], 2)})
        cov_rows.append({
            "project_gid": g, "name": pname.get(g, ""),
            "logged_hours": round(s["hours"], 2), "n_entries": s["n"],
            "entry_first": dates[0] if dates else "", "entry_last": dates[-1] if dates else "",
            "n_authors": len(s["authors"]),
            "n_tasks": tk[g]["n_tasks"] if g in tk else 0,
            "n_completed": tk[g]["n_completed"] if g in tk else 0,
            "task_completed_first": ca[0].date().isoformat() if ca else "",
            "task_completed_last": ca[-1].date().isoformat() if ca else "",
            "pct_activity_in_tracked_window": round(pct_in, 4),
            "coverage": label, "is_course_dev": "True" if is_cd else "False",
            "evidence": drift or (registry.get(g, {}).get("notes", "")[:200]),
        })
    write("audit_coverage.csv", cov_rows, list(cov_rows[0].keys()))

    # ---- archetypes.csv (straight from the registry) ---------------------------- #
    arch_rows = []
    for g, reg in registry.items():
        arch_rows.append({
            "gid": g, "name": reg["name"],
            "logged_hours": round(ent[g]["hours"], 2) if g in ent else 0,
            "n_tasks": tk[g]["n_tasks"] if g in tk else 0,
            "archetype": reg["archetype"], "n_videos": reg["n_videos"],
            "n_modules": reg["n_modules"], "n_animations": "",
            "other_deliverables": "", "evidence": reg.get("notes", "")[:300],
        })
    write("archetypes.csv", arch_rows, list(arch_rows[0].keys()))

    # ---- ground_truth.csv: the mechanical merge rule ----------------------------- #
    gt_rows = []
    for r in cov_rows:
        if norm_label(r["coverage"]) == "full" and norm_bool(r["is_course_dev"]) \
           and r["logged_hours"] >= 10.0:
            g = r["project_gid"]
            reg = registry.get(g, {})
            cs = cal_by_gid.get(g, {})
            gt_rows.append({
                "name": r["name"], "gid": g,
                "archetype": reg.get("archetype", ""),
                "logged_hours": r["logged_hours"],
                "n_videos": reg.get("n_videos", ""),
                "n_entries": r["n_entries"],
                "span_weeks": cs.get("span_weeks", ""),
                "span_reliable": cs.get("reliable", False),
                "coverage": r["coverage"],
            })
    gt_rows.sort(key=lambda r: -r["logged_hours"])
    write("ground_truth.csv", gt_rows, list(gt_rows[0].keys()))

    # ---- needs_review.csv ---------------------------------------------------------- #
    write("needs_review.csv", review, ["gid", "name", "logged_hours", "reason"])
    if review:
        print(f"\nATTENTION: {len(review)} project(s) need a human decision "
              "(see derived/needs_review.csv) -- they are EXCLUDED from the "
              "calibration until resolved. No guessing.")
    print(f"ground truth: {len(gt_rows)} projects, "
          f"{round(sum(r['logged_hours'] for r in gt_rows), 1)}h")


if __name__ == "__main__":
    main()
