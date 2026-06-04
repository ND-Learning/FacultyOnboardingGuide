import pandas as pd
import numpy as np

BASE = "/Users/ljt/Downloads/PM intern/odl_estimator/data_all"
TRACK_START = pd.Timestamp("2024-08-19", tz="UTC")

te = pd.read_csv(f"{BASE}/time_entries.csv", dtype={"project_gid": str, "task_gid": str})
tasks = pd.read_csv(f"{BASE}/tasks_raw.csv", dtype={"project_gid": str, "task_gid": str})

te["entry_date"] = pd.to_datetime(te["entry_date"], errors="coerce")
tasks["completed_at"] = pd.to_datetime(tasks["completed_at"], errors="coerce", utc=True)
tasks["created_at"] = pd.to_datetime(tasks["created_at"], errors="coerce", utc=True)

# pull_report bulk projects
bulk_names = set()
import re
try:
    with open(f"{BASE}/pull_report.txt") as f:
        rep = f.read()
    print("=== PULL REPORT ===")
    print(rep)
except Exception as e:
    print("no report", e)

rows = []
for gid, g in te.groupby("project_gid"):
    name = g["project_name"].iloc[0]
    logged_hours = g["hours"].sum()
    n_entries = len(g)
    first_entry = g["entry_date"].min()
    last_entry = g["entry_date"].max()
    n_authors = g["entry_author"].nunique()

    t = tasks[tasks["project_gid"] == gid]
    n_tasks = len(t)
    comp = t[t["completed"] == True]
    n_completed = len(comp)
    comp_dates = comp["completed_at"].dropna()
    first_comp = comp_dates.min() if len(comp_dates) else pd.NaT
    last_comp = comp_dates.max() if len(comp_dates) else pd.NaT
    if len(comp_dates):
        pct_in_window = (comp_dates >= TRACK_START).mean()
    else:
        pct_in_window = np.nan

    rows.append(dict(
        gid=gid, name=name.strip() if isinstance(name, str) else name,
        logged_hours=round(float(logged_hours), 2), n_entries=n_entries,
        entry_first=first_entry.date().isoformat() if pd.notna(first_entry) else "",
        entry_last=last_entry.date().isoformat() if pd.notna(last_entry) else "",
        n_authors=n_authors,
        n_tasks=n_tasks, n_completed=n_completed,
        task_completed_first=first_comp.date().isoformat() if pd.notna(first_comp) else "",
        task_completed_last=last_comp.date().isoformat() if pd.notna(last_comp) else "",
        pct_activity_in_tracked_window=round(float(pct_in_window), 4) if pd.notna(pct_in_window) else "",
        n_comp_with_date=len(comp_dates),
        in_tasks_raw=(n_tasks > 0),
    ))

df = pd.DataFrame(rows).sort_values("logged_hours", ascending=False)
pd.set_option("display.max_rows", 200)
pd.set_option("display.width", 250)
print("\n=== PER PROJECT (from time_entries, 55 projects) ===")
print(df.to_string(index=False))
print("\nN projects:", len(df))
print("Total logged hours:", df["logged_hours"].sum())
df.to_csv(f"{BASE}/derived/_audit_intermediate.csv", index=False)
