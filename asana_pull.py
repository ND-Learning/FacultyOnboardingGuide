#!/usr/bin/env python3
"""
asana_pull.py  --  ODL course-development effort estimator, data-extraction step.

Pulls historical ODL projects out of Asana and reconstructs a per-phase dataset
WITHOUT needing time tracking to have existed all along:

  * task created_at / completed_at timestamps  -> per-phase CALENDAR duration
  * memberships.section.name                   -> which phase a task belongs to
  * distinct assignees, task counts            -> effort-intensity proxies
  * custom fields                              -> size features captured at intake
  * time_tracking_entries (if your plan has it) -> real per-task EFFORT minutes
  * stories (optional)                         -> comment/activity volume proxy

Pure standard library (urllib + csv + json) -- no pip install required.

------------------------------------------------------------------------------
QUICK START
------------------------------------------------------------------------------
1. Make a Personal Access Token: Asana -> profile photo -> My Settings ->
   Apps -> Developer apps -> "Create new token". Then:

       export ASANA_TOKEN="0/your-token-here"

2. Confirm the token works and see your workspaces:

       python3 asana_pull.py whoami

3. Find the project GIDs you care about. A project's GID is in its URL:
   app.asana.com/0/<PROJECT_GID>/list  . Or list them:

       python3 asana_pull.py list-projects --workspace <WORKSPACE_GID>
       # the ODL Portfolio is a *portfolio*, list its contents with:
       python3 asana_pull.py portfolio-items --portfolio <PORTFOLIO_GID>

4. Pull. First pass (no crosswalk yet) discovers every section name:

       python3 asana_pull.py pull --portfolio <PORTFOLIO_GID> --out data/

   Open data/crosswalk_template.csv, fill in the canonical_phase column
   (map each Asana section to ONE phase name), save it as data/crosswalk.csv,
   then re-run to get clean per-phase rows:

       python3 asana_pull.py pull --portfolio <PORTFOLIO_GID> \
               --crosswalk data/crosswalk.csv --out data/

OUTPUT FILES (in --out dir):
   projects.csv          one row per project (dates, span, task counts, custom fields)
   tasks_raw.csv         one row per (project, task) -- the ground-truth extract
                         includes tcf::<field name> columns for task custom fields
   project_custom_fields.csv one row per project custom-field value
   task_custom_fields.csv    one row per task custom-field value
   phase_summary.csv     one row per (project, phase) -- the modelling table
   sections_seen.csv     every distinct section name found, with task counts
   crosswalk_template.csv section_name + blank canonical_phase, ready to fill
   pull_report.txt       what worked, what was skipped, time-tracking availability
"""

import argparse
import csv
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, date

API = "https://app.asana.com/api/1.0"


# --------------------------------------------------------------------------- #
# HTTP plumbing                                                               #
# --------------------------------------------------------------------------- #
def _token():
    tok = os.environ.get("ASANA_TOKEN", "").strip()
    if not tok:
        sys.exit("ERROR: set ASANA_TOKEN in your environment first "
                 "(export ASANA_TOKEN='0/...').")
    return tok


def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def _dump_csv(path, rows):
    """Write list-of-dicts to CSV (union of keys as header). Used for the final
    outputs and for periodic checkpoints during long pulls."""
    if not rows:
        return
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def api_get(path, params=None, sleep=0.2, max_retries=8):
    """GET one page. Returns (data, next_offset_or_None). Raises ApiError on
    non-retryable HTTP errors so callers can decide how to react."""
    params = dict(params or {})
    url = path if path.startswith("http") else API + path
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    headers = {"Authorization": "Bearer " + _token(),
               "Accept": "application/json"}
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if sleep:
                time.sleep(sleep)
            nxt = (payload.get("next_page") or {}).get("offset")
            return payload.get("data"), nxt
        except urllib.error.HTTPError as e:
            code = e.code
            retry_after = e.headers.get("Retry-After")
            body = ""
            try:
                body = e.read().decode("utf-8")
            except Exception:
                pass
            if code == 429 and attempt <= max_retries:
                wait = float(retry_after) if retry_after else min(60, 2 ** attempt)
                _log("  rate limited (429); sleeping %.0fs" % wait)
                time.sleep(wait)
                continue
            if code in (500, 502, 503, 504) and attempt <= max_retries:
                wait = min(30, 2 ** attempt)
                _log("  server %d; retrying in %.0fs" % (code, wait))
                time.sleep(wait)
                continue
            raise ApiError(code, body, url)
        except OSError as e:
            # OSError covers URLError, socket timeout, TimeoutError, conn resets.
            # (HTTPError is handled above; ApiError is not an OSError so it
            # propagates.) Retry transient network failures with backoff.
            if attempt <= max_retries:
                wait = min(45, 2 ** attempt)
                _log("  network error (%s); retrying in %.0fs" % (e, wait))
                time.sleep(wait)
                continue
            raise


class ApiError(Exception):
    def __init__(self, code, body, url):
        self.code = code
        self.body = body
        self.url = url
        super().__init__("HTTP %s for %s :: %s" % (code, url, body[:300]))


def api_get_all(path, params=None, sleep=0.2):
    """Follow pagination, return the full list."""
    out = []
    params = dict(params or {})
    params.setdefault("limit", 100)
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        data, offset = api_get(path, params, sleep=sleep)
        if data:
            out.extend(data)
        if not offset:
            break
    return out


# --------------------------------------------------------------------------- #
# Date helpers                                                                #
# --------------------------------------------------------------------------- #
def parse_dt(s):
    """Asana timestamp ('2024-07-18T13:00:00.000Z') or date ('2024-07-18')."""
    if not s:
        return None
    try:
        if "T" in s:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def to_date(dt):
    if dt is None:
        return None
    return dt.date() if isinstance(dt, datetime) else dt


def days_between(a, b):
    da, db = to_date(a), to_date(b)
    if da is None or db is None:
        return None
    return (db - da).days


# --------------------------------------------------------------------------- #
# Discovery sub-commands                                                      #
# --------------------------------------------------------------------------- #
def cmd_whoami(args):
    data, _ = api_get("/users/me",
                      {"opt_fields": "name,email,workspaces.name"})
    print("Authenticated as: %s <%s>" % (data.get("name"), data.get("email")))
    print("Workspaces:")
    for w in data.get("workspaces", []):
        print("  %-18s  %s" % (w["gid"], w.get("name")))


def cmd_list_projects(args):
    params = {"workspace": args.workspace,
              "opt_fields": "name,archived,created_at,team.name"}
    if args.archived is not None:
        params["archived"] = "true" if args.archived else "false"
    path = "/teams/%s/projects" % args.team if args.team else "/projects"
    rows = api_get_all(path, params)
    print("%-18s %-7s %-26s %s" % ("GID", "ARCH", "TEAM", "NAME"))
    for p in rows:
        print("%-18s %-7s %-26s %s" % (
            p["gid"], str(p.get("archived")),
            (p.get("team") or {}).get("name", "")[:26], p.get("name")))
    # optional machine-readable dump: one "gid  # name" line per project, so
    # refresh.py can regenerate all_gids.txt from the workspace without having
    # to parse the human-readable columns above.
    if getattr(args, "out_file", None):
        with open(args.out_file, "w") as f:
            for p in rows:
                name = (p.get("name") or "").replace("\n", " ").strip()
                f.write("%s  # %s\n" % (p["gid"], name))
        _log("wrote %s (%d projects, 'gid  # name' format)" % (args.out_file, len(rows)))
    print("\n%d projects." % len(rows), file=sys.stderr)


def cmd_discover(args):
    """One-shot: user gid + teams + portfolios, to pick a pull scope."""
    me, _ = api_get("/users/me",
                    {"opt_fields": "name,workspaces.name"})
    me_gid = me["gid"]
    print("user: %s (gid %s)" % (me.get("name"), me_gid))
    workspaces = me.get("workspaces", [])
    ws = args.workspace or (workspaces[0]["gid"] if workspaces else None)
    print("workspaces: %s" % ", ".join("%s=%s" % (w["gid"], w.get("name"))
                                       for w in workspaces))
    if not ws:
        return
    print("\n-- teams you belong to (workspace %s) --" % ws)
    try:
        teams = api_get_all("/users/me/teams",
                            {"organization": ws, "opt_fields": "name"})
        for t in teams:
            print("  team %-18s %s" % (t["gid"], t.get("name")))
    except ApiError as e:
        print("  (teams unavailable: HTTP %d)" % e.code)
    print("\n-- portfolios you own (workspace %s) --" % ws)
    try:
        ports = api_get_all("/portfolios",
                            {"workspace": ws, "owner": me_gid,
                             "opt_fields": "name"})
        for p in ports:
            print("  portfolio %-18s %s" % (p["gid"], p.get("name")))
        if not ports:
            print("  (none owned by you -- the ODL Portfolio may be owned by "
                  "someone else; use the team route or paste its GID)")
    except ApiError as e:
        print("  (portfolios unavailable: HTTP %d)" % e.code)


def cmd_portfolio_items(args):
    rows = api_get_all("/portfolios/%s/items" % args.portfolio,
                       {"opt_fields": "name,resource_type"})
    print("%-18s %-10s %s" % ("GID", "TYPE", "NAME"))
    for it in rows:
        print("%-18s %-10s %s" % (it["gid"], it.get("resource_type"),
                                  it.get("name")))
    projects = [it["gid"] for it in rows if it.get("resource_type") == "project"]
    print("\n%d items (%d projects)." % (len(rows), len(projects)))
    print("project GIDs: %s" % ",".join(projects))


# --------------------------------------------------------------------------- #
# Main pull                                                                   #
# --------------------------------------------------------------------------- #
TASK_FIELDS = ",".join([
    "name", "created_at", "completed", "completed_at",
    "assignee.name", "assignee.gid", "num_subtasks", "resource_subtype",
    "start_on", "due_on",
    "memberships.project.gid", "memberships.section.name",
    "custom_fields.gid", "custom_fields.name", "custom_fields.resource_subtype",
    "custom_fields.display_value", "custom_fields.text_value",
    "custom_fields.number_value", "custom_fields.enum_value.name",
    "custom_fields.multi_enum_values.name", "custom_fields.date_value.date",
    "custom_fields.people_value.name", "custom_fields.reference_value.name",
])

PROJECT_FIELDS = ",".join([
    "name", "archived", "created_at", "start_on", "due_on",
    "current_status.text", "owner.name", "team.name",
    "custom_fields.gid", "custom_fields.name", "custom_fields.resource_subtype",
    "custom_fields.display_value", "custom_fields.text_value",
    "custom_fields.number_value", "custom_fields.enum_value.name",
    "custom_fields.multi_enum_values.name", "custom_fields.date_value.date",
    "custom_fields.people_value.name", "custom_fields.reference_value.name",
])


def resolve_project_gids(args):
    gids = []
    if args.portfolio:
        items = api_get_all("/portfolios/%s/items" % args.portfolio,
                            {"opt_fields": "resource_type"})
        gids += [it["gid"] for it in items
                 if it.get("resource_type") == "project"]
    if args.projects:
        gids += [g.strip() for g in args.projects.split(",") if g.strip()]
    if args.project_file:
        with open(args.project_file) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    gids.append(line)
    # de-dup, preserve order
    seen, out = set(), []
    for g in gids:
        if g not in seen:
            seen.add(g)
            out.append(g)
    if not out:
        sys.exit("ERROR: no projects. Use --portfolio, --projects or "
                 "--project-file.")
    return out


def load_crosswalk(path):
    """section_name -> canonical_phase."""
    if not path:
        return {}
    cw = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            sec = (row.get("section_name") or "").strip()
            ph = (row.get("canonical_phase") or "").strip()
            if sec and ph:
                cw[sec] = ph
    return cw


def section_of(task, project_gid):
    for m in task.get("memberships", []) or []:
        if (m.get("project") or {}).get("gid") == project_gid:
            sec = m.get("section") or {}
            return sec.get("name") or "(no section)"
    return "(no section)"


def _compact_names(values):
    return ", ".join((v.get("name") or "") for v in (values or []) if v.get("name"))


def _custom_field_display(cf):
    """Return a stable export value for any Asana custom-field type."""
    if cf.get("display_value") not in (None, ""):
        return cf.get("display_value")
    subtype = cf.get("resource_subtype") or ""
    if subtype == "text":
        return cf.get("text_value") or ""
    if subtype == "number":
        return cf.get("number_value")
    if subtype == "enum":
        return (cf.get("enum_value") or {}).get("name", "")
    if subtype == "multi_enum":
        return _compact_names(cf.get("multi_enum_values"))
    if subtype == "date":
        return (cf.get("date_value") or {}).get("date", "")
    if subtype == "people":
        return _compact_names(cf.get("people_value"))
    if subtype == "reference":
        return _compact_names(cf.get("reference_value"))
    return ""


def custom_field_map(custom_fields):
    """Field name -> display/export value. Used for wide CSV columns."""
    out = {}
    for cf in custom_fields or []:
        name = (cf.get("name") or "").strip()
        if name:
            out[name] = _custom_field_display(cf)
    return out


def custom_field_rows(custom_fields, base):
    """Long-form custom field rows preserve every field even when names change."""
    rows = []
    for cf in custom_fields or []:
        name = (cf.get("name") or "").strip()
        if not name:
            continue
        date_value = cf.get("date_value") or {}
        rows.append(dict(base, **{
            "custom_field_gid": cf.get("gid") or "",
            "custom_field_name": name,
            "resource_subtype": cf.get("resource_subtype") or "",
            "display_value": _custom_field_display(cf),
            "text_value": cf.get("text_value") or "",
            "number_value": cf.get("number_value") if cf.get("number_value") is not None else "",
            "enum_value": (cf.get("enum_value") or {}).get("name", ""),
            "multi_enum_values": _compact_names(cf.get("multi_enum_values")),
            "date_value": date_value.get("date", "") or date_value.get("date_time", ""),
            "people_value": _compact_names(cf.get("people_value")),
            "reference_value": _compact_names(cf.get("reference_value")),
        }))
    return rows


def fetch_time_tracking(task_gid, available, sleep):
    """Returns (entries, still_available). entries = list of dicts
    {minutes, entered_on, author} — the individual logged time entries (a real
    timesheet). Probes once; disables on 402/403/404 so we stop hammering it."""
    if not available:
        return [], False
    try:
        rows = api_get_all("/tasks/%s/time_tracking_entries" % task_gid,
                           {"opt_fields": "duration_minutes,entered_on,created_by.name"},
                           sleep=sleep)
        entries = [{"minutes": float(r.get("duration_minutes") or 0),
                    "entered_on": r.get("entered_on") or "",
                    "author": (r.get("created_by") or {}).get("name", "")}
                   for r in rows]
        return entries, True
    except ApiError as e:
        if e.code in (402, 403, 404):
            _log("  time-tracking endpoint unavailable (HTTP %d) -- "
                 "skipping it for the rest of the run." % e.code)
            return [], False
        raise


def fetch_activity(task_gid, sleep):
    rows = api_get_all("/tasks/%s/stories" % task_gid,
                       {"opt_fields": "resource_subtype"}, sleep=sleep)
    comments = sum(1 for r in rows if r.get("resource_subtype") == "comment_added")
    return len(rows), comments


STATUS_UPDATE_FIELDS = "title,text,status_type,created_at,created_by.name"


def fetch_status_updates(project_gid, available, sleep):
    """The project's weekly STATUS UPDATES — the colored 'On track / At risk / Off
    track' narrative PMs post each week. Returns (updates, still_available). Probes
    once and disables on 402/403/404 (like time-tracking) so we don't hammer an
    endpoint the plan/permissions don't allow. Each update: created_at, status_type,
    title, text, author."""
    if not available:
        return [], False
    try:
        rows = api_get_all("/status_updates",
                           {"parent": project_gid, "opt_fields": STATUS_UPDATE_FIELDS},
                           sleep=sleep)
        updates = [{"created_at": r.get("created_at") or "",
                    "status_type": r.get("status_type") or "",
                    "title": (r.get("title") or "").strip(),
                    "text": (r.get("text") or "").strip(),
                    "author": (r.get("created_by") or {}).get("name", "")}
                   for r in rows]
        return updates, True
    except ApiError as e:
        if e.code in (402, 403, 404):
            _log("  status-updates endpoint unavailable (HTTP %d) -- "
                 "skipping it for the rest of the run." % e.code)
            return [], False
        raise


def _status_update_row(gid, pname, u):
    return {"project_gid": gid, "project_name": pname,
            "created_at": u["created_at"], "status_type": u["status_type"],
            "author": u["author"], "title": u["title"], "text": u["text"]}


def cmd_pull_status(args):
    """Standalone: pull ONLY the weekly project status updates -> status_updates.csv
    (fast; lets you test the feature without a full re-pull)."""
    os.makedirs(args.out, exist_ok=True)
    gids = resolve_project_gids(args)
    _log("Pulling status updates for %d projects..." % len(gids))
    su_available, rows = True, []
    for pi, gid in enumerate(gids, 1):
        try:
            proj, _ = api_get("/projects/%s" % gid, {"opt_fields": "name"})
            pname = proj.get("name")
        except Exception as e:
            _log("  project %s skipped (%s)" % (gid, e))
            continue
        updates, su_available = fetch_status_updates(gid, su_available, args.sleep)
        _log("  [%d/%d] %s: %d update(s)" % (pi, len(gids), pname, len(updates)))
        rows.extend(_status_update_row(gid, pname, u) for u in updates)
        if not su_available:
            _log("  (status-updates endpoint unavailable -- stopping)")
            break
    _write_csv(os.path.join(args.out, "status_updates.csv"), rows)
    _log("wrote %s/status_updates.csv (%d rows)" % (args.out, len(rows)))


def cmd_pull(args):
    os.makedirs(args.out, exist_ok=True)
    crosswalk = load_crosswalk(args.crosswalk)
    gids = resolve_project_gids(args)
    _log("Pulling %d projects..." % len(gids))

    tt_available = not args.no_time_tracking
    su_available = not getattr(args, "no_status_updates", False)
    status_update_rows = []
    report = []
    sections_seen = defaultdict(int)
    project_rows, project_custom_field_rows = [], []
    task_rows, task_custom_field_rows, time_entry_rows = [], [], []
    # phase aggregation: key (project_gid, phase) -> accumulator
    agg = defaultdict(lambda: {
        "n_tasks": 0, "n_completed": 0, "assignees": set(),
        "first_completed": None, "last_completed": None,
        "first_created": None, "last_created": None,
        "created_counter": defaultdict(int),
        "tracked_minutes": 0.0, "activity": 0, "comments": 0,
        "subtasks": 0,
    })

    for pi, gid in enumerate(gids, 1):
        if pi % 15 == 0 and time_entry_rows:
            _dump_csv(os.path.join(args.out, "time_entries.csv"), time_entry_rows)
            _log("  ...checkpoint saved (%d time entries so far)" % len(time_entry_rows))
        try:
            proj, _ = api_get("/projects/%s" % gid, {
                "opt_fields": PROJECT_FIELDS})
            pname = proj.get("name")
            _log("  [%d/%d] %s (%s)" % (pi, len(gids), pname, gid))
            tasks = api_get_all("/projects/%s/tasks" % gid,
                                {"opt_fields": TASK_FIELDS}, sleep=args.sleep)
        except Exception as e:
            _log("  [%d/%d] project %s SKIPPED (%s)" % (pi, len(gids), gid, e))
            report.append("project %s skipped: %s" % (gid, e))
            continue

        proj_first_done = proj_last_done = None
        custom = custom_field_map(proj.get("custom_fields"))
        project_custom_field_rows.extend(custom_field_rows(
            proj.get("custom_fields"),
            {"project_gid": gid, "project_name": pname}
        ))

        # weekly status updates (colored On track/At risk/Off track narrative)
        if su_available:
            updates, su_available = fetch_status_updates(gid, su_available, args.sleep)
            status_update_rows.extend(_status_update_row(gid, pname, u) for u in updates)

        for t in tasks:
            sec = section_of(t, gid)
            sections_seen[sec] += 1
            phase = crosswalk.get(sec, sec)
            created = parse_dt(t.get("created_at"))
            done = parse_dt(t.get("completed_at"))
            assignee = (t.get("assignee") or {}).get("name")
            tcustom = custom_field_map(t.get("custom_fields"))
            resp_team = tcustom.get("Responsible Team") or ""
            task_custom_field_rows.extend(custom_field_rows(
                t.get("custom_fields"),
                {
                    "project_gid": gid, "project_name": pname,
                    "task_gid": t["gid"], "task_name": t.get("name"),
                    "section": sec, "canonical_phase": phase,
                }
            ))

            tt_minutes = 0.0
            if tt_available:
                tt_entries, tt_available = fetch_time_tracking(
                    t["gid"], tt_available, args.sleep)
                tt_minutes = sum(e["minutes"] for e in tt_entries)
                for e in tt_entries:
                    time_entry_rows.append({
                        "project_gid": gid, "project_name": pname,
                        "task_gid": t["gid"], "task_name": t.get("name"),
                        "section": sec, "canonical_phase": phase,
                        "assignee": assignee or "",
                        "responsible_team": resp_team,
                        "entry_author": e["author"],
                        "entry_date": e["entered_on"],
                        "minutes": e["minutes"],
                        "hours": round(e["minutes"] / 60.0, 3),
                    })
            act = comm = 0
            if args.activity:
                act, comm = fetch_activity(t["gid"], args.sleep)
            task_row = {
                "project_gid": gid, "project_name": pname,
                "task_gid": t["gid"], "task_name": t.get("name"),
                "section": sec, "canonical_phase": phase,
                "completed": t.get("completed"),
                "created_at": t.get("created_at"),
                "completed_at": t.get("completed_at"),
                "assignee": assignee or "",
                "num_subtasks": t.get("num_subtasks") or 0,
                "resource_subtype": t.get("resource_subtype") or "",
                "tracked_minutes": tt_minutes,
                "n_activity": act, "n_comments": comm,
                "task_custom_fields": json.dumps(tcustom, ensure_ascii=False),
            }
            for k, v in tcustom.items():
                task_row["tcf::" + k] = v
            task_rows.append(task_row)

            a = agg[(gid, phase)]
            a["n_tasks"] += 1
            a["subtasks"] += (t.get("num_subtasks") or 0)
            a["tracked_minutes"] += tt_minutes
            a["activity"] += act
            a["comments"] += comm
            if assignee:
                a["assignees"].add(assignee)
            if created:
                a["created_counter"][created.isoformat()] += 1
                a["first_created"] = min(a["first_created"] or created, created)
                a["last_created"] = max(a["last_created"] or created, created)
            if t.get("completed") and done:
                a["n_completed"] += 1
                a["first_completed"] = min(a["first_completed"] or done, done)
                a["last_completed"] = max(a["last_completed"] or done, done)
                proj_first_done = min(proj_first_done or done, done)
                proj_last_done = max(proj_last_done or done, done)

        prow = {
            "project_gid": gid, "project_name": pname,
            "archived": proj.get("archived"),
            "created_at": proj.get("created_at"),
            "start_on": proj.get("start_on"), "due_on": proj.get("due_on"),
            "owner": (proj.get("owner") or {}).get("name", ""),
            "team": (proj.get("team") or {}).get("name", ""),
            "n_tasks": len(tasks),
            "first_completed": proj_first_done.isoformat() if proj_first_done else "",
            "last_completed": proj_last_done.isoformat() if proj_last_done else "",
            "span_days_completed": days_between(proj_first_done, proj_last_done),
            "span_days_start_due": days_between(parse_dt(proj.get("start_on")),
                                                parse_dt(proj.get("due_on"))),
        }
        for k, v in custom.items():
            prow["cf::" + k] = v
        project_rows.append(prow)

    # ---- write tasks_raw.csv ---------------------------------------------- #
    _write_csv(os.path.join(args.out, "tasks_raw.csv"), task_rows)

    # ---- write time_entries.csv (the actual timesheet) -------------------- #
    _write_csv(os.path.join(args.out, "time_entries.csv"), time_entry_rows)

    # ---- write status_updates.csv (weekly project updates) ---------------- #
    # Always emit a well-formed CSV (header row even when there are zero updates,
    # e.g. the endpoint was unavailable) so the dashboard's weekly-update risk
    # detector never has to read a 0-byte / headerless file.
    _write_csv_with_header(
        os.path.join(args.out, "status_updates.csv"), status_update_rows,
        ["project_gid", "project_name", "created_at", "status_type",
         "author", "title", "text"])

    # ---- write projects.csv ----------------------------------------------- #
    _write_csv(os.path.join(args.out, "projects.csv"), project_rows)

    # ---- write long-form custom field values ------------------------------ #
    _write_csv(os.path.join(args.out, "project_custom_fields.csv"),
               project_custom_field_rows)
    _write_csv(os.path.join(args.out, "task_custom_fields.csv"),
               task_custom_field_rows)

    # ---- write phase_summary.csv ------------------------------------------ #
    phase_rows = []
    for (gid, phase), a in agg.items():
        pname = next((p["project_name"] for p in project_rows
                      if p["project_gid"] == gid), gid)
        bulk = max(a["created_counter"].values()) if a["created_counter"] else 0
        bulk_frac = round(bulk / a["n_tasks"], 2) if a["n_tasks"] else 0
        phase_rows.append({
            "project_gid": gid, "project_name": pname, "canonical_phase": phase,
            "n_tasks": a["n_tasks"], "n_completed": a["n_completed"],
            "n_assignees": len(a["assignees"]),
            "first_completed": a["first_completed"].isoformat() if a["first_completed"] else "",
            "last_completed": a["last_completed"].isoformat() if a["last_completed"] else "",
            "span_days": days_between(a["first_completed"], a["last_completed"]),
            "tracked_minutes": round(a["tracked_minutes"], 1),
            "tracked_hours": round(a["tracked_minutes"] / 60.0, 2),
            "total_subtasks": a["subtasks"],
            "n_activity": a["activity"], "n_comments": a["comments"],
            "bulk_created_fraction": bulk_frac,  # >0.5 => timestamps unreliable
        })
    phase_rows.sort(key=lambda r: (r["project_name"], r["canonical_phase"]))
    _write_csv(os.path.join(args.out, "phase_summary.csv"), phase_rows)

    # ---- sections + crosswalk template ------------------------------------ #
    sec_rows = [{"section_name": s, "n_tasks": n}
                for s, n in sorted(sections_seen.items(),
                                   key=lambda kv: -kv[1])]
    _write_csv(os.path.join(args.out, "sections_seen.csv"), sec_rows)
    cw_rows = [{"section_name": s, "canonical_phase": crosswalk.get(s, "")}
               for s in sorted(sections_seen)]
    _write_csv(os.path.join(args.out, "crosswalk_template.csv"), cw_rows)

    # ---- report ----------------------------------------------------------- #
    n_tracked_projects = len({r["project_gid"] for r in phase_rows
                              if r["tracked_minutes"] > 0})
    n_project_custom_fields = len({r["custom_field_name"]
                                   for r in project_custom_field_rows})
    n_task_custom_fields = len({r["custom_field_name"]
                                for r in task_custom_field_rows})
    bulk_projects = sorted({r["project_name"] for r in phase_rows
                            if r["bulk_created_fraction"] > 0.5})
    lines = [
        "ODL Asana pull report",
        "=====================",
        "projects pulled        : %d" % len(project_rows),
        "tasks pulled           : %d" % len(task_rows),
        "phase rows             : %d" % len(phase_rows),
        "distinct sections      : %d" % len(sections_seen),
        "project custom fields  : %d distinct names (%d values)" %
        (n_project_custom_fields, len(project_custom_field_rows)),
        "task custom fields     : %d distinct names (%d values)" %
        (n_task_custom_fields, len(task_custom_field_rows)),
        "time-tracking endpoint : %s" % (
            "AVAILABLE" if (not args.no_time_tracking and tt_available)
            else "UNAVAILABLE / disabled"),
        "projects with logged hours : %d" % n_tracked_projects,
        "crosswalk applied      : %s" % (args.crosswalk or "NONE (raw section names used)"),
        "",
        "WARN: projects where >50%% of tasks share one created_at (bulk "
        "creation -> created_at unreliable, lean on completed_at): %s"
        % (", ".join(bulk_projects) or "none"),
        "",
        "NEXT: open crosswalk_template.csv, fill 'canonical_phase' for each "
        "section, save as crosswalk.csv, and re-run with --crosswalk.",
    ]
    if report:
        lines += ["", "skips:"] + ["  " + r for r in report]
    txt = "\n".join(lines)
    with open(os.path.join(args.out, "pull_report.txt"), "w") as f:
        f.write(txt + "\n")
    _log("\n" + txt)
    _log("\nWrote outputs to %s/" % args.out)


def _write_csv_with_header(path, rows, header):
    """Like _write_csv but writes an explicit header even when rows is empty, so
    consumers always get a parseable CSV (not a 0-byte file)."""
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    _log("  wrote %s (%d rows)" % (path, len(rows)))


def _write_csv(path, rows):
    if not rows:
        # still create an empty file so downstream steps don't choke
        open(path, "w").close()
        _log("  wrote %s (0 rows)" % path)
        return
    cols = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    _log("  wrote %s (%d rows)" % (path, len(rows)))


# --------------------------------------------------------------------------- #
# CLI                                                                         #
# --------------------------------------------------------------------------- #
def build_parser():
    p = argparse.ArgumentParser(
        description="Extract ODL historical projects from Asana for the "
                    "course-development effort estimator.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="verify token, list workspaces"
                   ).set_defaults(func=cmd_whoami)

    dsc = sub.add_parser("discover", help="list your gid, teams, portfolios")
    dsc.add_argument("--workspace", default=None)
    dsc.set_defaults(func=cmd_discover)

    lp = sub.add_parser("list-projects", help="list projects in a workspace/team")
    lp.add_argument("--workspace", required=True)
    lp.add_argument("--team", default=None)
    lp.add_argument("--archived", type=lambda s: s.lower() == "true",
                    default=None, help="true|false (omit for both)")
    lp.add_argument("--out-file", default=None,
                    help="also write the list to this file as 'gid  # name' lines")
    lp.set_defaults(func=cmd_list_projects)

    pi = sub.add_parser("portfolio-items", help="list items inside a portfolio")
    pi.add_argument("--portfolio", required=True)
    pi.set_defaults(func=cmd_portfolio_items)

    pl = sub.add_parser("pull", help="extract projects -> CSVs")
    pl.add_argument("--portfolio", default=None,
                    help="pull every project in this portfolio")
    pl.add_argument("--projects", default=None,
                    help="comma-separated project GIDs")
    pl.add_argument("--project-file", default=None,
                    help="file with one project GID per line (# comments ok)")
    pl.add_argument("--crosswalk", default=None,
                    help="CSV mapping section_name -> canonical_phase")
    pl.add_argument("--out", default="data", help="output directory")
    pl.add_argument("--no-time-tracking", action="store_true",
                    help="skip the time_tracking_entries calls entirely")
    pl.add_argument("--no-status-updates", action="store_true",
                    help="skip the project status_updates calls")
    pl.add_argument("--activity", action="store_true",
                    help="also pull per-task stories (slow: 1 call/task)")
    pl.add_argument("--sleep", type=float, default=0.2,
                    help="seconds between API calls (raise if rate-limited)")
    pl.set_defaults(func=cmd_pull)

    ps = sub.add_parser("pull-status",
                        help="pull ONLY weekly project status updates -> status_updates.csv")
    ps.add_argument("--portfolio", default=None)
    ps.add_argument("--projects", default=None)
    ps.add_argument("--project-file", default=None)
    ps.add_argument("--out", default="data")
    ps.add_argument("--sleep", type=float, default=0.2)
    ps.set_defaults(func=cmd_pull_status)
    return p


def main():
    args = build_parser().parse_args()
    try:
        args.func(args)
    except ApiError as e:
        sys.exit("\nAPI ERROR: %s" % e)


if __name__ == "__main__":
    main()
