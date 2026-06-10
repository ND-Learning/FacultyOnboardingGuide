# ODL Effort Estimator — Calibration Report

**Date:** 2026-06-04 · **Pipeline:** `asana_pull.py → data_all/ → calibrate.py → calibration.json → inject_calibration.py → faculty_guide/estimator.js`

Every number below is computed from ODL's own Asana data. Where the data cannot
support a number, the model says **"not calibratable"** instead of guessing.

---

## 1. The data

| | |
|---|---|
| Real time entries | **2,335 entries · 2,053.0 h** (`time_entries.csv`) |
| Window | 2024-08-19 → 2026-06-02 (tracking began Aug 2024) |
| Projects with logged time | 55 |
| **Calibration set** | **25 projects · 1,696.7 h (82.6%)** — full logging coverage, course-dev, ≥10 h |
| Calendar ground truth | `completed_at` spans, 56 of 90 projects reliable after bulk-check-off filtering |

Selection rule for the calibration set (applied mechanically, see
`data_all/derived/ground_truth.csv`):

```
coverage == "full"        # ≥90% of task completions inside the tracked window
                          # AND logging cadence habitual across the activity period
AND is_course_dev         # not an internal/admin/template board
AND logged_hours >= 10
```

## 2. The key structural finding: PM is logged differently

```
                 % of project's logged hours that are PM
  Radio Systems  ████████████████████████  96.5%   ┐
  NEON           █████████████████████████ 100%    │ boards WITH a dedicated
  Calc I Summer  ██████████████████████    89.5%   │ "PM Time Tracking" section
  3D Environmts  █████████████████████     86.5%   │ (10 boards, 679 h)
  ASCEND Eng     ██████████████████        73.3%   ┘
  ...
  23 other boards ≥10h                      0%     ← no PM section → no PM logged
```

A flat "PM = 33% of effort" would be an **artifact of who used a PM section**.
So the calibration splits the data into two independent models:

- **Production effort** (non-PM hours) — from the 21 calibration projects with ≥10 h of non-PM logging.
- **PM effort** — only from PM-tracked projects, as a *rate per calendar week*.

## 3. The formulas

**Production effort by archetype** (empirical quantiles, `statistics.quantiles(n=20, method="inclusive")`):

```
hours(archetype) ~ {P25, P50, P75, P80}  of  Σ non-PM logged hours per project
```

| Archetype | n | P25 | **P50** | P75 | P80 | range |
|---|---|---|---|---|---|---|
| full_course | 14 | 21.5 | **42.8** | 65.6 | 73.0 | 15.7–286.5 |
| video_series | 2 | — | **42.0** | — | — | raw values: 29.2, 54.8 |
| course_redesign | 2 | — | **32.8** | — | — | raw values: 32.3, 33.4 |
| single_video | 3 | — | **12.8** | — | — | raw values: 12.0, 12.8, 15.5 |

(For n<4, interpolated quartiles are withheld — raw values shown instead.)

**PM model** (n=14 PM-tracked projects with reliable calendar spans):

```
PM_hours = calendar_weeks × rate_pm
rate_pm  ∈ {P25: 0.6, P50: 0.8, P75: 1.6, P80: 2.0, max: 3.9} h/week
```

The estimator now computes `pm = [weeks_lo × 0.6, weeks_hi × 1.6]`.
This **replaces** the old extrapolated XL bucket of 150–300 h — the largest PM
total ever actually logged is 162 h ("AI for the ND Community", 42 wk × 3.9 h/wk).

**Per-video unit rate** (shrinkage blend per the README design):

```
rate = w · observed_P50 + (1 − w) · board_prior ,   w = n / (n + 3)
     = 0.62 × 12.8 + 0.38 × 8.5  =  11.2 h / finished short video
```

observed = 5 fully-logged video units (PIE #2 15.5h, Ultrasound 12.8h, Borgo
12.0h, Radio MVP avg 13.7h, Videos-for-Eva avg 7.3h); prior = median of ODL
board lecture-band per-video rates (8.5 h). The board's lecture rates are
**validated** by the data; interview/testimonial (34–41 h) and mini-doc (70 h)
remain unvalidated — no fully-logged exemplar yet.

**Calendar duration** (completed-task spans; bulk check-offs excluded):

| Archetype | n | P25 | **P50** | P75 | weeks |
|---|---|---|---|---|---|
| full_course | 23 | 17.7 | **31.4** | 41.9 | (old guide said ≈13 wk — real courses run ~2.4× longer) |
| xr_interactive | 4 | 10.8 | **16.0** | 22.0 | |
| video_series | 4 | 9.6 | **12.1** | 14.3 | |
| course_redesign | 3 | 9.1 | **10.3** | 16.8 | |
| single_video | 4 | 2.4 | **3.9** | 7.2 | |

**Full-course phase mix** (841.3 h pooled non-PM hours, 14 projects):

```
Production 31.8% ▏███████████▌
Post-Prod  22.0% ▏████████
Dev/Build  18.5% ▏██████▋
Pre-Prod   18.3% ▏██████▋
Design      8.9% ▏███▏
Discovery   0.5% ▏▏          ← discovery happens before boards get tracked
```

## 4. Honesty checks

**Adversarial verification (multi-agent):** the audit that built the ground-truth
set passed 12/12 spot-checks against raw CSVs. The calibration itself was then
attacked by three independent verifiers: (1) a full re-implementation from the
written spec — **all 10 blocks reproduced exactly, "could not be refuted"**;
(2) a code audit of `calibrate.py` — 3 findings, all fixed (slash-compound
sections now routed by task name, which moved ~26 h of course-build work from
Post-Production to Development/Build; n<4 quartiles withheld; single-exemplar
media split labeled as such); (3) chart/output sanity — all numbers reproduce,
3 chart legibility defects fixed.

**Leave-one-out backtest** (full-course production hours): median APE of a P50
point guess = **60.3%**, IQR coverage 42.9%. That spread is the finding: a
point estimate would be a lie — which is why the estimator quotes **P25–P80
ranges** and they will tighten only as per-task tracking accumulates.

**Not calibratable (and therefore not invented):**
- **Faculty time** — 0 faculty hours in the data (staff time only)
- Discovery (11 h logged total), QA (≈0 h), Evaluation (1 h)
- Interview / testimonial / mini-doc / XR unit rates (board values stand, unvalidated)
- Per-module rates (module counts exist for 5/19 projects and are not comparable)

## 5. Evidence (charts in `data_all/derived/charts/`)

1. `1_project_hours.png` — calibration set, PM vs production split per project
2. `2_full_course_hours.png` — full-course hour distribution with P25/P50/P80
3. `3_video_rates.png` — observed per-video hours vs ODL board rates + blended rate
4. `4_pm_model.png` — PM hours vs calendar weeks, with P25/P50/P75 rate lines
5. `5_calendar.png` — span by archetype (boxplots, reliable spans only)
6. `6_phase_mix.png` — where full-course hours actually go

## 6. What changed in the faculty guide

- `estimator.js` gains a machine-generated `CALIBRATION` block (never hand-edited)
  and a `estimateFromHistory(type)` API: real-project quartiles alongside the
  bundle math.
- PM line item now `weeks × 0.6–1.6 h/wk` (was size buckets, XL extrapolated).
- The plan page shows "**What similar real ODL projects took**" with n's.
- All uncalibrated figures remain explicitly labeled planning estimates.

## 7. Refresh procedure (automated)

One command runs the whole loop — pull → deterministic audit → calibrate →
inject → rebuild bundle → publish to Canvas → delta report:

```bash
python3 refresh.py                  # full run (see refresh_config.json)
python3 refresh.py --skip-pull      # recompute from the existing pull
./install_schedule.sh               # schedule it daily at 07:30 (launchd)
```

The judgment layer is `project_registry.csv` (archetype + course-dev labels,
frozen from the verified 2026-06-04 audit). The nightly job **never re-guesses
labels**: new hours on known projects flow in automatically; brand-new projects
are excluded and queued in `data_all/derived/needs_review.csv` until someone
adds one registry row; a fully-tracked project whose logging stops while tasks
keep completing is demoted and queued (re-admit with `force_coverage=full`).
The deterministic audit (`audit_rules.py`) reproduces the agent-verified
baseline calibration exactly (0 differences).

## 8. Performance & impact (Asana Impact Tracker, added 2026-06-10)

Two calibration blocks feed the faculty guide's **Performance** tab; both are
recomputed on every refresh and flow through the same no-hand-typing path
(`calibrate.py` → `calibration.json` → `inject_calibration.py`).

**Block G — project-level status** (`impact_tracker`): coverage and freshness
of the `Impact Tracker Status` custom field across all pulled projects
(`data_all/projects.csv`), including the list of projects flagged *Outdated*.

**Block H — the Impact Tracker board** (`impact_custom_fields`): the ODL
*Impact Tracker* Asana board (gid 1211592424221769, registered
`internal_admin` / non-course-dev so it never enters effort calibration) holds
one task per past/current project with **38 custom fields**. The pull exports
every task custom field to `data_all/task_custom_fields.csv`; block H scopes
metric extraction to that board's rows and derives:

- **Outcomes** — Faculty Satisfaction Index and Net Promoter Score
  (median/mean/n), Student Reach / Year (empty in Asana as of 2026-06-10).
- **Assets delivered** — per record it prefers the `Total Assets` rollup and
  falls back to summing the per-type counts (Videos, XR experiences, Graphics,
  Canvas Courses, Interactives, Web Page Modules) — never both, so no double
  counting.
- **Documentation compliance** — % *Yes* among applicable (non-N/A) projects
  for IP Agreement, Project Charter, Handoff Document, Post-Project
  Evaluation, MOU.
- **Status mix** of the board (`Complete` / `In Progress` / …).
- **Hours cross-check** — tracker-reported `Total Hours` vs hours actually
  logged in Asana time entries for the same project (matched by GID or unique
  normalized name); reported as a ratio, since tracker hours predate
  systematic time logging.
- **Hours-per-asset model** — `Total Assets` joined to full-coverage logged
  projects; used by the estimator only as a labeled cross-check, activating at
  n≥4 matched projects.

Same honesty rules as everything above: blank Asana fields are reported as
missing, never imputed; survey-style results stay summary-level (directors own
the raw survey data per SOP).
