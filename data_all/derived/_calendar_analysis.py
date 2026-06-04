import pandas as pd
import numpy as np
import os, json

BASE = "/Users/ljt/Downloads/PM intern/odl_estimator/data_all"
DERIVED = os.path.join(BASE, "derived")
os.makedirs(DERIVED, exist_ok=True)

df = pd.read_csv(os.path.join(BASE, "tasks_raw.csv"))

# completed timestamps
df["completed_at"] = pd.to_datetime(df["completed_at"], utc=True, errors="coerce")

# completed tasks = completed flag True AND a valid completed_at
comp = df[(df["completed"] == True) & (df["completed_at"].notna())].copy()

# minute-rounded completion
comp["minute"] = comp["completed_at"].dt.floor("min")

# ---------- phase mapping ----------
def map_phase(section):
    if not isinstance(section, str):
        s = ""
    else:
        s = section.lower()
    def has(*subs):
        return any(sub in s for sub in subs)
    # first match wins, in EXACT order specified
    if has("pm time", "pm tracking"):
        return None  # skip cross-phase
    if has("post-project", "post project", "evaluation", "retro", "reflection", "survey", "handoff", "delivery"):
        return "Evaluation"
    if has("post-production", "post prod", "post-prod", "editing", "edit"):
        return "Post-Production"
    if has("pre-production", "pre prod", "pre-prod", "script", "storyboard"):
        return "Pre-Production"
    if has("design development", "course build", "build", "develop", "assessment", "lms", "canvas"):
        return "Development/Build"
    if has("production", "film", "shoot", "record", "studio"):
        return "Production"
    if has("media", "video", "animation", "graphic"):
        return "Production"
    if has("qa", "launch", "quality"):
        return "QA & Launch"
    if has("design", "course map", "map", "content", "objectives"):
        return "Design"
    if has("analysis", "intake", "kickoff", "charter", "discovery", "planning"):
        return "Discovery"
    return "Other"

comp["phase"] = comp["section"].apply(map_phase)

# ---------- per-project span ----------
proj_rows = []
# group by gid to avoid name collisions; carry the name
for gid, g in comp.groupby("project_gid"):
    name = g["project_name"].iloc[0]
    n_completed = len(g)
    first = g["completed_at"].min()
    last = g["completed_at"].max()
    span_days = (last - first).total_seconds() / 86400.0
    # bulk_completion_frac = max fraction sharing same minute
    minute_counts = g["minute"].value_counts()
    bulk_frac = minute_counts.iloc[0] / n_completed if n_completed > 0 else np.nan
    reliable = (n_completed >= 5) and (bulk_frac <= 0.5) and (span_days > 0)
    proj_rows.append({
        "project_gid": gid,
        "name": name,
        "n_completed": n_completed,
        "span_days": round(span_days, 3),
        "span_weeks": round(span_days / 7.0, 3),
        "bulk_completion_frac": round(float(bulk_frac), 4),
        "first_completed": first.isoformat(),
        "last_completed": last.isoformat(),
        "reliable": bool(reliable),
    })

proj_df = pd.DataFrame(proj_rows).sort_values("span_days", ascending=False)
proj_df.to_csv(os.path.join(DERIVED, "calendar_spans.csv"), index=False)

# ---------- per-(project, phase) spans ----------
reliable_gids = set(proj_df[proj_df["reliable"]]["project_gid"])

phase_rows = []
for (gid, phase), g in comp[comp["phase"].notna()].groupby(["project_gid", "phase"]):
    name = g["project_name"].iloc[0]
    n = len(g)
    minute_counts = g["minute"].value_counts()
    bf = minute_counts.iloc[0] / n if n > 0 else np.nan
    if n < 3 or bf > 0.5:
        continue
    first = g["completed_at"].min()
    last = g["completed_at"].max()
    sp = (last - first).total_seconds() / 86400.0
    phase_rows.append({
        "project_gid": gid,
        "name": name,
        "phase": phase,
        "n_completed": n,
        "span_days": round(sp, 3),
        "bulk_completion_frac": round(float(bf), 4),
        "project_reliable": gid in reliable_gids,
        "first_completed": first.isoformat(),
        "last_completed": last.isoformat(),
    })

phase_df = pd.DataFrame(phase_rows).sort_values(["phase", "span_days"])
phase_df.to_csv(os.path.join(DERIVED, "phase_spans.csv"), index=False)

# ---------- per-phase stats across RELIABLE projects ----------
phase_stats = []
PHASE_ORDER = ["Discovery", "Design", "Development/Build", "Pre-Production",
               "Production", "Post-Production", "QA & Launch", "Evaluation", "Other"]
rel_phase = phase_df[phase_df["project_reliable"]]
for phase in PHASE_ORDER:
    sub = rel_phase[rel_phase["phase"] == phase]
    if len(sub) == 0:
        continue
    vals = sub["span_days"].values
    phase_stats.append({
        "phase": phase,
        "n_projects": int(len(sub)),
        "median_span_days": round(float(np.median(vals)), 2),
        "p25_days": round(float(np.percentile(vals, 25)), 2),
        "p75_days": round(float(np.percentile(vals, 75)), 2),
    })

# ---------- course-dev-looking reliable projects: total span in weeks ----------
# A project is "course-dev-looking" if among its completed tasks it has at least one
# phase mapping into the core course-dev pipeline phases.
COURSE_DEV_PHASES = {"Discovery", "Design", "Development/Build", "Pre-Production",
                     "Production", "Post-Production", "QA & Launch", "Evaluation"}
proj_phase_sets = comp[comp["phase"].notna()].groupby("project_gid")["phase"].apply(lambda s: set(s))
cd_gids = set()
for gid, pset in proj_phase_sets.items():
    if pset & COURSE_DEV_PHASES:
        cd_gids.add(gid)

cd_reliable = proj_df[(proj_df["reliable"]) & (proj_df["project_gid"].isin(cd_gids))]
wk = cd_reliable["span_weeks"].values
week_dist = {
    "n": int(len(wk)),
    "median": round(float(np.median(wk)), 2),
    "p25": round(float(np.percentile(wk, 25)), 2),
    "p75": round(float(np.percentile(wk, 75)), 2),
    "min": round(float(np.min(wk)), 2),
    "max": round(float(np.max(wk)), 2),
}

print("=== SUMMARY ===")
print("total projects (by gid):", len(proj_df))
print("reliable projects:", int(proj_df["reliable"].sum()))
print("course-dev reliable projects:", len(cd_reliable))
print()
print("WEEK DIST (course-dev reliable):", json.dumps(week_dist))
print()
print("PHASE STATS:")
for ps in phase_stats:
    print(ps)
print()
print("=== PROJECTS JSON ===")
out_projs = []
for _, r in proj_df.iterrows():
    ev = f"n_completed={r['n_completed']}, bulk_frac={r['bulk_completion_frac']}, {r['first_completed'][:10]}..{r['last_completed'][:10]}"
    out_projs.append({
        "name": r["name"],
        "span_days": float(r["span_days"]),
        "span_weeks": float(r["span_weeks"]),
        "n_completed": int(r["n_completed"]),
        "bulk_completion_frac": float(r["bulk_completion_frac"]),
        "reliable": bool(r["reliable"]),
        "evidence": ev,
    })
print(json.dumps(out_projs[:5], indent=1))
print("... total", len(out_projs))

# persist intermediate JSON for the structured output assembly
with open(os.path.join(DERIVED, "_intermediate.json"), "w") as f:
    json.dump({
        "projects": out_projs,
        "phase_stats": phase_stats,
        "week_dist": week_dist,
        "n_total": int(len(proj_df)),
        "n_reliable": int(proj_df["reliable"].sum()),
        "n_cd_reliable": int(len(cd_reliable)),
    }, f)
