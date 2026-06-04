# ODL Effort Estimator — Handoff / Succession Guide

*For whoever inherits this after Juntong's internship. Last updated 2026-06-04.*

## What this is, in one paragraph

A faculty-facing planning guide (embedded in Canvas) that estimates course-dev
effort, timeline, PM hours, and faculty time — with every number **calibrated
from ODL's own logged Asana time** (no invented figures; see `CALIBRATION.md`).
A refresh loop re-pulls Asana, re-calibrates, and re-publishes to Canvas
automatically, so the estimates keep tightening as ODL logs more hours.

## Where things run (pick ONE home for the engine)

| Home | How | Status |
|---|---|---|
| **GitHub + Actions** (recommended) | push this folder to a repo (github.nd.edu or github.com, private), add `ASANA_TOKEN` + `CANVAS_TOKEN` as Actions secrets, commit `refresh_config.json`. `.github/workflows/refresh.yml` then runs nightly in the cloud — no laptop involved. | ready, needs the repo + secrets |
| A staff Mac | `./install_schedule.sh` (launchd, daily 07:30; only runs while the Mac is awake) | working today |
| ND server cron | `30 7 * * * cd /path/to/odl_estimator && python3 refresh.py` | ask OIT |

Canvas itself only hosts the **output** (one HTML file) — it cannot run the
pipeline. A PII-safe archive zip of this project can live in Canvas Files for
continuity (`python3 package_handoff.py`, then upload `dist/*.zip` to an
ODL-internal course), but the engine must live in one of the homes above.

## Taking over: the 30-minute checklist

1. Get the code: clone the repo (or unzip the handoff zip).
2. Tokens (yours, not your predecessor's — theirs die when their account closes):
   - Asana: My Settings → Apps → Developer apps → new token
   - Canvas: Account → Settings → + New Access Token
   - locally: `security add-generic-password -a "$USER" -s asana_token -w '…'`
     (same for `canvas_token`); on GitHub: repo Settings → Actions secrets.
3. `cp refresh_config.example.json refresh_config.json`, fill:
   `asana_portfolio_gid` (find it: `python3 asana_pull.py discover`),
   Canvas `course_id` + `page_url` (the page that embeds the guide).
4. Test: `python3 refresh.py --dry-run`, then `python3 refresh.py`.
5. Schedule it (GitHub Actions = nothing to do; Mac = `./install_schedule.sh`).

## Your one recurring duty (~2 min, whenever it appears)

After each refresh, read `refresh_report.txt` (or the Actions job summary).
If it says **ACTION NEEDED**, a new project started logging time (or a tracked
one drifted). It is **excluded from the calibration until you classify it**:
add/edit one row in `project_registry.csv` (gid, archetype, is_course_dev —
evidence is in `data_all/derived/needs_review.csv`), then re-run. This is
deliberate: the pipeline updates *numbers* automatically but never *judgment*.

## Invariants — do not break these

- **No guessing.** If data can't support a number, it stays `not_calibratable`
  and the guide shows a labeled planning estimate. Never fill a gap by hand.
- **Nobody edits generated things**: the `CALIBRATION` block in `estimator.js`,
  `calibration.json`, the Canvas bundle — all machine-written. Edit inputs
  (registry, rates board, scripts), then re-run `refresh.py`.
- **`data_all/derived/agent_baseline/` is frozen** — it's the verified audit
  the registry seeds from. Don't regenerate or delete it. (Lives in the repo /
  the with-data zip; the PII-safe zip ships without any `data_all/`, which is
  fine — you only need the baseline if you ever re-seed the registry with
  `make_registry.py`, and the shipped `project_registry.csv` already carries
  those labels.)
- **Tokens never go in files.** Keychain / env / Actions secrets only.
- **Raw time entries are ODL-internal** (per-person hours). The faculty-facing
  bundle ships only aggregate statistics; keep it that way. Faculty-facing
  numbers route through the Director per ODL policy.

## Known gaps a successor could close

1. **Faculty time** — zero hours logged; the one uncalibrated estimate.
   Plan: mine staff-logged joint sessions (filming/script reviews) as a lower
   bound + 15-min retrospective interviews with leads per project.
2. Interview/testimonial/mini-doc/XR unit rates — board values, unvalidated.
3. Per-module rates — needs module-granular logging (SOP change).
4. AI intake ("describe your course" → type/size → this same engine) and Pria
   as the words layer — see `faculty_guide/CANVAS_DEPLOY.md`, Layer 2.

## Map of the system

```
project_registry.csv ──┐                       (human judgment, frozen labels)
Asana API ──pull──► data_all/*.csv             (raw: entries, tasks)
                       │ audit_rules.py        (deterministic audit + drift)
                       ▼
        data_all/derived/ground_truth.csv      (calibration set)
                       │ calibrate.py          (all math, provenance, charts)
                       ▼
        data_all/derived/calibration.json
                       │ inject_calibration.py (machine-writes estimator.js)
                       ▼
        faculty_guide/estimator.js + index.html
                       │ build_canvas_bundle.py
                       ▼
        canvas_upload/ODL_Faculty_Onboarding_Guide.html
                       │ canvas_push.py        (Canvas API: replace + heal iframe)
                       ▼
        Canvas course page (faculty see this)
```
