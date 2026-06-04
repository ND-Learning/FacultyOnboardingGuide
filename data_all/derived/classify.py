import pandas as pd
import numpy as np

BASE = "/Users/ljt/Downloads/PM intern/odl_estimator/data_all"
TRACK_START = pd.Timestamp("2024-08-19", tz="UTC")

df = pd.read_csv(f"{BASE}/derived/_audit_intermediate.csv", dtype={"gid": str})
te = pd.read_csv(f"{BASE}/time_entries.csv", dtype={"project_gid": str})
te["entry_date"] = pd.to_datetime(te["entry_date"], errors="coerce")

# distinct months of logging per project (cadence signal)
months = te.groupby("project_gid")["entry_date"].apply(lambda s: s.dt.to_period("M").nunique())

# --- not-course-dev set (internal/admin/program-ops/tracking boards) ---
# Judged from task content inspected:
not_course_dev = {
    "Michael's PM Tasks",          # internal PM admin: find rooms, agendas, dashboards
    "Media Studio Sessions",       # single "Filming time tracker" task = tracking board
    "Summer Online 2025",          # program ops: advising mtgs, marketing, tuition, grades
    "Summer Online 2026",          # 2 placeholder time tasks ("Bri Time","Sonia Time")
    "ODL Podcast",                 # internal ODL podcast initiative (weekly date sections)
    "ODL Lookbook",                # internal ODL marketing/portfolio asset (RC/FC review cycles)
}

def classify(r):
    name = r["name"]
    lh = r["logged_hours"]
    ne = r["n_entries"]
    pct = r["pct_activity_in_tracked_window"]
    nc = r["n_completed"]
    ef = pd.to_datetime(r["entry_first"]) if r["entry_first"] else pd.NaT
    el = pd.to_datetime(r["entry_last"]) if r["entry_last"] else pd.NaT
    tf = pd.to_datetime(r["task_completed_first"]) if r["task_completed_first"] else pd.NaT
    tl = pd.to_datetime(r["task_completed_last"]) if r["task_completed_last"] else pd.NaT

    # minimal first
    if lh < 5 or ne < 5:
        return "minimal", f"logged_hours={lh}, n_entries={ne} (<5h or <5 entries) -> unusable as project total"

    pctv = float(pct) if pct != "" and pd.notna(pct) else np.nan

    # partial: pre-window activity OR logging covers only a slice
    reasons = []
    # pre-window activity
    if not np.isnan(pctv) and pctv < 0.9:
        reasons.append(f"{pctv:.0%} of completions in tracked window (pre-2024-08-19 activity undercounted)")
    # logging slice vs activity span
    if pd.notna(ef) and pd.notna(el) and pd.notna(tf) and pd.notna(tl):
        act_span = (tl - tf).days
        log_span = (el - ef).days
        # plausibility: very low hours vs many completed tasks
        if act_span > 120 and log_span >= 0 and log_span < 0.4 * act_span and nc >= 10:
            reasons.append(f"logging window {log_span}d covers only part of activity span {act_span}d (n_completed={nc})")

    # one-off cadence: all entries clustered in a single calendar month while
    # tasks complete across a much longer span -> not habitual logging
    nmonths = int(months.get(r["gid"], 0))
    if nmonths <= 1 and pd.notna(tf) and pd.notna(tl) and (tl - tf).days > 45 and nc >= 10:
        reasons.append(f"all {ne} entries in {nmonths} calendar month vs task activity span {(tl-tf).days}d (one-off logging, not habitual)")

    if reasons:
        return "partial", "; ".join(reasons)

    # full: >=90% completions in window and habitual cadence
    # cadence check: entries spread across activity, not one-off
    if not np.isnan(pctv) and pctv >= 0.9:
        nmonths = int(months.get(r["gid"], 0))
        return "full", f"{pctv:.0%} of completions in tracked window; {ne} entries across {nmonths} months ({r['entry_first']}..{r['entry_last']}) = habitual cadence"
    # tasks have no completed dates but enough logging
    if np.isnan(pctv):
        return "partial", f"no completed_at dates for tasks; cannot confirm window coverage (n_completed={nc})"
    return "partial", "uncategorized; defaulting partial"

out = []
for _, r in df.iterrows():
    cov, ev = classify(r)
    is_cd = r["name"] not in not_course_dev
    out.append(dict(
        project_gid=r["gid"],
        name=r["name"],
        logged_hours=r["logged_hours"],
        n_entries=int(r["n_entries"]),
        entry_first=r["entry_first"],
        entry_last=r["entry_last"],
        n_authors=int(r["n_authors"]),
        n_tasks=int(r["n_tasks"]),
        n_completed=int(r["n_completed"]),
        task_completed_first=r["task_completed_first"],
        task_completed_last=r["task_completed_last"],
        pct_activity_in_tracked_window=r["pct_activity_in_tracked_window"],
        coverage=cov,
        is_course_dev=is_cd,
        evidence=ev,
    ))

res = pd.DataFrame(out)
res.to_csv(f"{BASE}/derived/audit_coverage.csv", index=False)
pd.set_option("display.max_rows", 200); pd.set_option("display.width", 300)
print(res[["name","logged_hours","n_entries","coverage","is_course_dev"]].to_string(index=False))
print("\ncoverage counts:\n", res["coverage"].value_counts())
print("\nis_course_dev counts:\n", res["is_course_dev"].value_counts())
print("\nWrote", f"{BASE}/derived/audit_coverage.csv", "rows:", len(res))
