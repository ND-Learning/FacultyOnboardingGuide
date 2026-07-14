#!/usr/bin/env python3
"""
Regenerate data_all/timesheet_ws.csv from the Asana WORKSPACE time_tracking_entries
endpoint, which returns entries for ALL users (unlike the old per-task puller, which
only captured entries on tasks it happened to pull, missing most people).

Standalone script. Stdlib only (urllib, json, csv, subprocess, datetime). Does not
touch asana_pull.py, build.py, data_all/time_entries.csv, or any other file in this
repo -- the estimator's calibration needs time_entries.csv left completely alone.
"""
import csv
import datetime
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

WORKSPACE_GID = "228221773618853"
API_URL = "https://app.asana.com/api/1.0/time_tracking_entries"
OPT_FIELDS = (
    "entered_on,duration_minutes,created_by.name,"
    "task.name,task.gid,task.projects.name,task.projects.gid"
)
START_DATE = "2024-07-01"
PAGE_LIMIT = 100

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data_all")
CSV_PATH = os.path.join(DATA_DIR, "timesheet_ws.csv")

HEADER = [
    "project_gid", "project_name", "task_gid", "task_name",
    "section", "canonical_phase", "assignee", "responsible_team",
    "entry_author", "entry_date", "minutes", "hours",
]


def get_token():
    token = os.environ.get("ASANA_TOKEN")
    if token:
        return token.strip()
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", "asana_token", "-w"],
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except subprocess.CalledProcessError as e:
        sys.exit(f"ERROR: could not retrieve Asana token from env or keychain: {e}")


def fetch_all_entries(token, start_date, end_date):
    entries = []
    offset = None
    page = 0
    while True:
        params = {
            "workspace": WORKSPACE_GID,
            "entered_on_start_date": start_date,
            "entered_on_end_date": end_date,
            "opt_fields": OPT_FIELDS,
            "limit": str(PAGE_LIMIT),
        }
        if offset:
            params["offset"] = offset
        url = f"{API_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            sys.exit(f"ERROR: HTTP {e.code} on page {page} (offset={offset}): {detail}")
        data = body.get("data", [])
        entries.extend(data)
        page += 1
        next_page = body.get("next_page")
        if next_page and next_page.get("offset"):
            offset = next_page["offset"]
        else:
            break
    return entries


def to_row(entry):
    task = entry.get("task") or {}
    projects = task.get("projects") or []
    first_project = projects[0] if projects else {}
    created_by = entry.get("created_by") or {}
    minutes = entry.get("duration_minutes") or 0
    hours = round(minutes / 60, 2)
    return {
        "project_gid": first_project.get("gid", ""),
        "project_name": first_project.get("name", ""),
        "task_gid": task.get("gid", ""),
        "task_name": task.get("name", ""),
        "section": "",
        "canonical_phase": "",
        "assignee": "",
        "responsible_team": "",
        "entry_author": created_by.get("name", ""),
        "entry_date": entry.get("entered_on", ""),
        "minutes": minutes,
        "hours": hours,
    }


def main():
    token = get_token()
    start_date = START_DATE
    end_date = datetime.date.today().isoformat()
    print(f"Pulling workspace time entries {start_date} .. {end_date} ...")

    raw_entries = fetch_all_entries(token, start_date, end_date)
    print(f"Fetched {len(raw_entries)} raw entries from Asana.")

    rows = [to_row(e) for e in raw_entries]

    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {CSV_PATH}")

    cutoff = "2026-06-01"
    totals = {}
    for r in rows:
        if r["entry_date"] and r["entry_date"] >= cutoff:
            totals[r["entry_author"]] = totals.get(r["entry_author"], 0) + (r["hours"] or 0)

    print(f"\nPer-person total hours since {cutoff}:")
    for name, hrs in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {name or '(blank)'}: {round(hrs, 2)}")


if __name__ == "__main__":
    main()
