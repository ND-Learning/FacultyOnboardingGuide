# ODL Course-Development Effort Estimator

Goal: an AI-nested tool where a faculty member describes the course they want,
and it returns **effort (hours/phase)**, a **calendar timeline**, the
**faculty's own time commitment**, and **suggestions** — grounded in ODL's own
historical projects.

**Status: calibrated and self-sustaining.** The estimator ships in
`faculty_guide/` (Canvas-embeddable single file), its numbers come from real
logged time (see `CALIBRATION.md`), and `refresh.py` keeps everything current
as new Asana time entries accumulate.

## Core design principles
- **LLM touches words; a transparent parametric model touches numbers.** The
  LLM does intake + clarifying questions + explanation. A plain Python function
  does all arithmetic (`hours = Σ count_i × rate_i` per phase). The LLM may
  *describe* a number but never *invents* one.
- **No guessing, ever.** Every shipped number traces to data rows with stated
  provenance; where data is insufficient the model says `not_calibratable`
  instead of inventing a figure (see `CALIBRATION.md`).
- **Ranges, not points** (P25/P50/P80) that tighten as data accumulates.
- **Segment by archetype** (full course / video series / sprint / XR); never
  average incomparable unit economics.
- **Humans own judgment, the pipeline owns arithmetic.** Classification labels
  live in `project_registry.csv`; the automated refresh never re-guesses them.

## Three data tiers
| Tier | Source | Coverage | Use |
|------|--------|----------|-----|
| A | project start/due dates | all projects | weak prior, sanity band |
| B | task `created_at`/`completed_at` per section | all template boards | per-phase **calendar** duration |
| C | `time_tracking_entries` (real minutes) | the tracked projects (currently 25 fully tracked — see `CALIBRATION.md`) | **ground truth** effort, overwrites A/B |

External **reference-class priors** (ODL's own unit-rate board; Chapman
Alliance benchmarks as backstop) seed any rate with little internal data,
blended in via shrinkage `w = n/(n+3)`.

## The sustainability loop (start here)

One command — or the daily scheduled job — runs the whole chain:

```
refresh.py:  pull (Asana API) → audit_rules.py (deterministic, no re-guessing)
             → calibrate.py → calibration.json → inject_calibration.py
             → estimator.js → build_canvas_bundle.py → canvas_push.py (Canvas API)
             → refresh_report.txt (deltas + action items)
```

```bash
# one-time setup
security add-generic-password -a "$USER" -s asana_token  -w '<ASANA_TOKEN>'   # Asana > My Settings > Apps
security add-generic-password -a "$USER" -s canvas_token -w '<CANVAS_TOKEN>'  # Canvas > Account > Settings
cp refresh_config.example.json refresh_config.json   # fill portfolio gid (asana_pull.py discover) + course/page
./install_schedule.sh                                 # daily at 07:30 via launchd

# manual runs
python3 refresh.py                  # full refresh + publish
python3 refresh.py --skip-pull      # recompute from the existing pull
python3 refresh.py --dry-run        # show the plan, change nothing
```

**Human-in-the-loop, by design:** new hours on known projects flow in
automatically. A **new project** is excluded and queued in
`data_all/derived/needs_review.csv` until someone adds one row to
`project_registry.csv` (archetype + is_course_dev — ~2 minutes). A tracked
project whose logging stops while tasks keep completing is demoted and queued;
re-admit with `force_coverage=full`. Check `refresh_report.txt` after each run.

## Manual pull (one-off / debugging)
```bash
export ASANA_TOKEN="<YOUR_TOKEN>"           # or let refresh.py read the keychain
python3 asana_pull.py whoami                # confirm token + see workspace GIDs
python3 asana_pull.py discover              # find the portfolio GID
python3 asana_pull.py pull --portfolio <PORTFOLIO_GID> --out data_all/
```
Outputs: `projects.csv`, `tasks_raw.csv`, `time_entries.csv`, `phase_summary.csv`,
`sections_seen.csv`, `crosswalk_template.csv`, `pull_report.txt`.
`pull_report.txt` tells you whether the **time-tracking endpoint is enabled**
and flags bulk-created timestamps. (The old `--crosswalk` two-pass flow is
legacy/optional: the pipeline now derives phases from keyword rules inside
`calibrate.py` / `audit_rules.py`, so no crosswalk is needed.)

## Key files
| File | Role |
|---|---|
| `CALIBRATION.md` | the calibration report — formulas, numbers, provenance, verification record |
| `project_registry.csv` | human-curated labels (frozen from the verified 2026-06-04 audit) |
| `audit_rules.py` | deterministic audit — rebuilds the calibration set each refresh |
| `calibrate.py` | all the math → `data_all/derived/calibration.json` + charts |
| `inject_calibration.py` | machine-writes the CALIBRATION block in `faculty_guide/estimator.js` |
| `refresh.py` / `install_schedule.sh` | the loop + its daily schedule |
| `canvas_push.py` | publishes the bundle to Canvas, self-heals the page iframe |
| `data_all/derived/agent_baseline/` | frozen outputs of the original multi-agent audit (registry seeds from here only) |

## Roadmap (updated)
- ~~Phase 0–1: pull, calibrate, back-test~~ → **done**, see `CALIBRATION.md`.
- ~~Phase 5: self-improvement loop~~ → **done** (`refresh.py` + registry workflow).
- **Phase 2** (ongoing): keep logging time by phase AND role/deliverable — every
  new entry tightens the ranges automatically.
- **Phase 3**: faculty-time backfill (15-min lead interviews per project) +
  co-presence mining — faculty hours are the one remaining `not_calibratable`.
- **Phase 4**: AI intake ("describe your course" → type/size → same engine), and
  Pria as the words layer (see `faculty_guide/CANVAS_DEPLOY.md`).

Numbers are **estimates, not commitments** — faculty-facing output routes
through the Director per ODL policy. Strip charter PII (faculty names,
stipends) before any external LLM call. Tokens live in the keychain, never in
files or this repo.
