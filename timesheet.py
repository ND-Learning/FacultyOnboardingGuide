#!/usr/bin/env python3
"""
timesheet.py -- build a real timesheet + calibration views from a pull that
captured per-entry time tracking (data_all/time_entries.csv).

Outputs (in --dir):
  timesheet.csv             clean per-entry rows sorted by date (the timesheet)
  by_person.csv             hours per person (and inferred role)
  by_phase.csv              hours per canonical phase (+ % of logged time)
  by_role.csv               hours per role (responsible_team where set, else inferred)
  by_project.csv            logged hours per project
And prints a CALIBRATION READOUT: phase distribution, per-project hour stats,
and an HONEST coverage note (logged time is partial / Media-concentrated, and
contains ODL STAFF time, NOT faculty time).

Usage: python3 timesheet.py --dir data_all
"""
import argparse, csv, os
from collections import defaultdict
from datetime import datetime

# Known ODL people -> role (extend as needed). Names not here -> "(unmapped)".
ROLE_MAP = {
    "Michael Lerma": "PM", "Annie Conaghan": "PM",
    # Lawrence has his own "PM Time Tracking: Lawrence" section -> PM
    "Lawrence Greenspun": "PM",
    "Briana Stines": "Learning Design", "Brianna Stines": "Learning Design",
    "Kevin DeCloedt": "Media", "KC Frye": "Media",
    "Kuangchen Hsu": "Learning Design",
    # Frequent loggers seen in the data (confirm with the team):
    "Matthew Simmons": "(confirm: Media?)",
    "Yi Lu": "(confirm: Media?)",
    "Alyssa Neece": "(confirm)",
    "John Corba": "(confirm)", "Colin Gallagher": "(confirm)",
}

def phase_of(sec):
    s = (sec or "").lower()
    checks = [
        (("pm time", "pm tracking"), "Project Mgmt (cross-phase)"),
        (("ld time",), "Learning Design (cross-phase)"),
        (("post-project", "post project", "evaluation", "retro", "reflection", "survey", "handoff", "delivery"), "Evaluation"),
        (("post-production", "post prod", "post-prod"), "Post-Production"),
        (("editing", "edit"), "Post-Production"),
        (("pre-production", "pre prod", "pre-prod", "script", "storyboard"), "Pre-Production"),
        (("design development", "course build", "build", "develop", "assessment", "lms", "canvas"), "Development/Build"),
        (("production", "film", "shoot", "record", "studio"), "Production"),
        (("media", "video", "animation", "graphic"), "Production"),
        (("qa", "launch", "quality"), "QA & Launch"),
        (("design", "course map", "map", "content", "objectives"), "Design"),
        (("analysis", "intake", "kickoff", "charter", "discovery", "planning"), "Discovery"),
    ]
    for keys, ph in checks:
        if any(k in s for k in keys):
            return ph
    return "Other / unsorted"

def role_of(author, assignee, resp_team, section=""):
    s = (section or "").lower()
    if "pm time" in s or "pm tracking" in s:
        return "PM"
    if "ld time" in s:
        return "Learning Design"
    if resp_team:
        return resp_team
    for name in (author, assignee):
        if name in ROLE_MAP:
            return ROLE_MAP[name]
    return "(unmapped)"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data_all")
    args = ap.parse_args()
    p = os.path.join(args.dir, "time_entries.csv")
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        raise SystemExit("No time_entries.csv in %s (did the pull finish?)" % args.dir)
    rows = list(csv.DictReader(open(p, newline="")))

    for r in rows:
        r["_hours"] = float(r.get("hours") or 0)
        ph = phase_of(r.get("section"))
        if ph == "Other / unsorted":          # fall back to the task name
            ph = phase_of(r.get("task_name"))
        r["_phase"] = ph
        r["_role"] = role_of(r.get("entry_author"), r.get("assignee"),
                             r.get("responsible_team"), r.get("section"))

    total = sum(r["_hours"] for r in rows)
    dates = [r.get("entry_date") for r in rows if r.get("entry_date")]
    by_person = defaultdict(float); by_phase = defaultdict(float)
    by_role = defaultdict(float); by_project = defaultdict(float)
    for r in rows:
        by_person[r.get("entry_author") or r.get("assignee") or "(unknown)"] += r["_hours"]
        by_phase[r["_phase"]] += r["_hours"]
        by_role[r["_role"]] += r["_hours"]
        by_project[r.get("project_name", "?").strip()] += r["_hours"]

    def write(name, header, items):
        with open(os.path.join(args.dir, name), "w", newline="") as f:
            w = csv.writer(f); w.writerow(header)
            for k, v in sorted(items, key=lambda kv: -kv[1]):
                w.writerow([k, round(v, 2)])

    # clean sorted timesheet
    rows_sorted = sorted(rows, key=lambda r: (r.get("entry_date") or "", r.get("project_name") or ""))
    cols = ["entry_date","entry_author","assignee","responsible_team","_role",
            "project_name","_phase","section","task_name","hours"]
    with open(os.path.join(args.dir, "timesheet.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows_sorted: w.writerow(r)
    write("by_person.csv", ["person","hours"], by_person.items())
    write("by_phase.csv", ["phase","hours"], by_phase.items())
    write("by_role.csv", ["role","hours"], by_role.items())
    write("by_project.csv", ["project","hours"], by_project.items())

    # ---- readout ----
    print("===== TIMESHEET SUMMARY =====")
    print("entries: %d | total logged: %.1f h | projects with time: %d"
          % (len(rows), total, sum(1 for v in by_project.values() if v > 0)))
    if dates:
        print("date range: %s -> %s" % (min(dates)[:10], max(dates)[:10]))
    print("\n--- by PHASE (calibration: where logged effort falls) ---")
    for ph, h in sorted(by_phase.items(), key=lambda kv: -kv[1]):
        print("  %-20s %8.1f h  %5.1f%%" % (ph, h, 100*h/total if total else 0))
    print("\n--- by ROLE (who logs; faculty are NOT here) ---")
    for ro, h in sorted(by_role.items(), key=lambda kv: -kv[1]):
        print("  %-22s %8.1f h  %5.1f%%" % (ro, h, 100*h/total if total else 0))
    print("\n--- per-project logged hours (distribution) ---")
    vals = sorted(v for v in by_project.values() if v > 0)
    if vals:
        import statistics
        print("  n=%d projects | min %.1f | median %.1f | mean %.1f | max %.1f"
              % (len(vals), vals[0], statistics.median(vals), sum(vals)/len(vals), vals[-1]))
    print("\n--- top 12 projects by logged hours ---")
    for proj, h in sorted(by_project.items(), key=lambda kv: -kv[1])[:12]:
        print("  %7.1f h  %s" % (h, proj))
    print("\nNOTE: logged time = ODL STAFF hours, partial & Media-weighted. It does"
          "\nNOT contain faculty hours. Use it to calibrate the ODL team-effort side"
          "\n(esp. media); faculty time still needs team estimation.")
    print("\nWrote: timesheet.csv, by_person.csv, by_phase.csv, by_role.csv, by_project.csv")

if __name__ == "__main__":
    main()
