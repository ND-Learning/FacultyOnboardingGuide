#!/usr/bin/env python3
"""
reaggregate.py -- post-process a pull WITHOUT re-hitting the Asana API.

Reads <dir>/tasks_raw.csv (the expensive extract, incl. time-tracking) plus an
optional crosswalk.csv (section_name -> canonical_phase) and rebuilds richer
per-phase tables. Use this to iterate on the phase taxonomy for free after a
single full pull.

Adds three things the live pull can't cheaply do:
  1. applies the canonical-phase crosswalk
  2. flags BULK COMPLETION (tasks checked off en masse at project close, which
     fakes a 0-day phase span) -- not just bulk creation
  3. sums the "Estimated time" custom field and rolls hours up by
     "Responsible Team" (PM / Design / Media / Graphics) where present

Usage:
  python3 reaggregate.py --dir data_archived [--crosswalk data_archived/crosswalk.csv]

Outputs (in --dir):
  phase_summary_v2.csv        one row per (project, canonical_phase)
  phase_team_summary.csv      one row per (project, canonical_phase, team)
  project_rollup.csv          one row per project (totals + data-quality flags)
"""
import argparse
import csv
import json
import os
import re
from collections import defaultdict
from datetime import datetime


def to_hours(s):
    """'8h 30m' / '45m' / '1h 30m' -> float hours; '' / None -> None."""
    if not s:
        return None
    h = re.search(r"(\d+)\s*h", s)
    m = re.search(r"(\d+)\s*m", s)
    if not h and not m:
        return None
    return round((int(h.group(1)) if h else 0) + (int(m.group(1)) if m else 0) / 60.0, 3)


def parse_dt(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def load_crosswalk(path):
    cw = {}
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                sec = (row.get("section_name") or "").strip()
                ph = (row.get("canonical_phase") or "").strip()
                if sec and ph:
                    cw[sec] = ph
    return cw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--crosswalk", default=None)
    args = ap.parse_args()

    cw = load_crosswalk(args.crosswalk)
    tasks_path = os.path.join(args.dir, "tasks_raw.csv")
    with open(tasks_path, newline="") as f:
        tasks = list(csv.DictReader(f))

    phase = defaultdict(lambda: {
        "n_tasks": 0, "n_completed": 0, "assignees": set(),
        "tracked_min": 0.0, "est_hours": 0.0, "est_seen": 0,
        "completed_at": [], "team_min": defaultdict(float),
        "team_est": defaultdict(float),
    })
    proj_name = {}
    for t in tasks:
        gid = t["project_gid"]
        proj_name[gid] = t["project_name"]
        sec = t.get("section") or "(no section)"
        ph = cw.get(sec, sec if not cw else "(unmapped) " + sec)
        cf = {}
        try:
            cf = json.loads(t.get("task_custom_fields") or "{}")
        except json.JSONDecodeError:
            pass
        team = (cf.get("Responsible Team") or "").strip() or "(unassigned)"
        est = to_hours(cf.get("Estimated time"))
        tt = float(t.get("tracked_minutes") or 0)

        a = phase[(gid, ph)]
        a["n_tasks"] += 1
        a["assignees"].add(t.get("assignee") or "")
        a["tracked_min"] += tt
        a["team_min"][team] += tt
        if est is not None:
            a["est_hours"] += est
            a["est_seen"] += 1
            a["team_est"][team] += est
        if (t.get("completed") or "").lower() == "true":
            a["n_completed"] += 1
            d = parse_dt(t.get("completed_at"))
            if d:
                a["completed_at"].append(d)

    # ---- phase_summary_v2 + bulk-completion detection -------------------- #
    phase_rows, team_rows = [], []
    proj = defaultdict(lambda: {"tracked": 0.0, "est": 0.0, "tasks": 0,
                                "bulk_phases": 0, "phases": 0})
    for (gid, ph), a in sorted(phase.items()):
        ca = sorted(a["completed_at"])
        span = (ca[-1] - ca[0]).days if len(ca) >= 2 else (0 if ca else None)
        # bulk completion: fraction of completed tasks sharing the SAME minute
        bulk_frac = 0.0
        if ca:
            buckets = defaultdict(int)
            for d in ca:
                buckets[d.replace(second=0, microsecond=0)] += 1
            bulk_frac = round(max(buckets.values()) / len(ca), 2)
        reliable = "no" if (bulk_frac > 0.5 or span == 0) and a["n_completed"] > 2 else "yes"
        phase_rows.append({
            "project_gid": gid, "project_name": proj_name[gid], "canonical_phase": ph,
            "n_tasks": a["n_tasks"], "n_completed": a["n_completed"],
            "n_assignees": len([x for x in a["assignees"] if x]),
            "span_days": span,
            "tracked_hours": round(a["tracked_min"] / 60.0, 2),
            "est_hours": round(a["est_hours"], 2) if a["est_seen"] else "",
            "bulk_completion_frac": bulk_frac,
            "span_reliable": reliable,
        })
        p = proj[gid]
        p["tracked"] += a["tracked_min"] / 60.0
        p["est"] += a["est_hours"]
        p["tasks"] += a["n_tasks"]
        p["phases"] += 1
        if reliable == "no":
            p["bulk_phases"] += 1
        for team, mins in a["team_min"].items():
            if mins or a["team_est"].get(team):
                team_rows.append({
                    "project_gid": gid, "project_name": proj_name[gid],
                    "canonical_phase": ph, "team": team,
                    "tracked_hours": round(mins / 60.0, 2),
                    "est_hours": round(a["team_est"].get(team, 0.0), 2),
                })

    rollup_rows = [{
        "project_gid": gid, "project_name": proj_name[gid],
        "n_tasks": p["tasks"], "n_phases": p["phases"],
        "total_tracked_hours": round(p["tracked"], 2),
        "total_est_hours": round(p["est"], 2),
        "phases_with_unreliable_span": p["bulk_phases"],
        "has_tracked_time": "yes" if p["tracked"] > 0 else "no",
    } for gid, p in sorted(proj.items(), key=lambda kv: -kv[1]["tracked"])]

    def write(name, rows):
        path = os.path.join(args.dir, name)
        cols = []
        for r in rows:
            for k in r:
                if k not in cols:
                    cols.append(k)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        print("wrote %s (%d rows)" % (path, len(rows)))

    write("phase_summary_v2.csv", phase_rows)
    write("phase_team_summary.csv", team_rows)
    write("project_rollup.csv", rollup_rows)

    n_tracked = sum(1 for r in rollup_rows if r["has_tracked_time"] == "yes")
    print("\n%d projects, %d with logged hours. Total tracked: %.1f h, total "
          "estimated: %.1f h." % (
              len(rollup_rows), n_tracked,
              sum(r["total_tracked_hours"] for r in rollup_rows),
              sum(r["total_est_hours"] for r in rollup_rows)))


if __name__ == "__main__":
    main()
