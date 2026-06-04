#!/usr/bin/env python3
"""
refresh.py -- the sustainability loop. One command (or one scheduled job) that
keeps the estimator current as new time entries accumulate in Asana:

  1. PULL      asana_pull.py        fresh time entries + tasks  (needs ASANA_TOKEN)
  2. AUDIT     audit_rules.py       deterministic rules + project_registry.csv
  3. CALIBRATE calibrate.py         quartiles/rates -> calibration.json + charts
  4. INJECT    inject_calibration.py -> faculty_guide/estimator.js
  5. BUNDLE    build_canvas_bundle.py -> canvas_upload/...html
  6. PUBLISH   canvas_push.py       replace the file on Canvas + fix the page iframe
                                    (needs CANVAS_TOKEN; skipped unless configured)
  7. REPORT    refresh_report.txt   what changed since last run + review queue

No guessing is preserved end to end: new hours on KNOWN projects flow in
automatically; NEW projects are excluded and queued in needs_review.csv until a
human adds one registry row. The report tells you when that queue is non-empty.

Usage:
  python3 refresh.py                       # full run (pull + publish if configured)
  python3 refresh.py --skip-pull           # recompute from existing data_all/
  python3 refresh.py --skip-push           # everything except Canvas upload
  python3 refresh.py --dry-run             # show what would happen, change nothing

Config: refresh_config.json (see refresh_config.example.json).
Tokens come from the environment or macOS keychain -- NEVER from files:
  ASANA_TOKEN   or keychain item:  security add-generic-password -a "$USER" -s asana_token  -w '<token>'
  CANVAS_TOKEN  or keychain item:  security add-generic-password -a "$USER" -s canvas_token -w '<token>'
"""
import argparse, csv, json, os, re, shutil, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))


def keychain(service):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def token(env_name, keychain_service):
    return os.environ.get(env_name, "").strip() or keychain(keychain_service)


def run(cmd, env=None, dry=False):
    print(f"\n$ {' '.join(cmd)}")
    if dry:
        print("  (dry-run: skipped)")
        return 0
    e = dict(os.environ)
    if env:
        e.update(env)
    r = subprocess.run(cmd, cwd=HERE, env=e)
    if r.returncode != 0:
        sys.exit(f"FAILED ({r.returncode}): {' '.join(cmd)} -- aborting refresh, "
                 "previous outputs left untouched where possible.")
    return r.returncode


def snapshot_numbers(cal_path):
    """The handful of headline numbers we report deltas on."""
    if not os.path.exists(cal_path):
        return {}
    c = json.load(open(cal_path))
    fc = c["archetype_effort_hours"].get("full_course", {}).get("production_hours", {})
    hours = re.search(r"([\d.]+)h", c["_provenance"]["source"])
    return {
        "total_logged_hours": hours.group(1) if hours else "?",
        "calibration_set": c["_provenance"]["calibration_set"].split(" ")[0],
        "full_course_p50_h": fc.get("p50"),
        "full_course_n": fc.get("n"),
        "pm_p50_h_per_wk": c["pm_model"]["pm_hours_per_week"].get("p50"),
        "video_blended_h": c["video_unit_rates"]["blended_generic_video_p50"]["value"],
        "full_course_median_weeks": c["calendar"]["span_weeks_by_archetype"]
                                     .get("full_course", {}).get("p50"),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "refresh_config.json"))
    ap.add_argument("--skip-pull", action="store_true")
    ap.add_argument("--skip-push", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config)) if os.path.exists(args.config) else {}
    data_dir = os.path.join(HERE, cfg.get("data_dir", "data_all"))
    cal_path = os.path.join(data_dir, "derived", "calibration.json")
    before = snapshot_numbers(cal_path)
    py = sys.executable or "python3"

    # Zero-context guard: if there is no pulled data AND this run won't pull,
    # explain the situation in plain language instead of letting audit_rules
    # crash with a traceback. (The PII-safe handoff zip ships without data_all/
    # on purpose -- a fresh pull rebuilds it.)
    have_data = os.path.exists(os.path.join(data_dir, "time_entries.csv"))
    will_pull = (not args.skip_pull
                 and (token("ASANA_TOKEN", "asana_token") and cfg.get("asana_portfolio_gid")))
    if not have_data and not will_pull:
        sys.exit(
            "\nNo data found ({}/time_entries.csv is missing) and this run will not "
            "pull any.\nThis is normal if you started from the PII-safe handoff zip -- "
            "it ships without data.\nTo fix (see README 'one-time setup' / HANDOFF.md "
            "30-minute checklist):\n"
            "  1. store a token:  security add-generic-password -a \"$USER\" -s asana_token -w '<token>'\n"
            "  2. cp refresh_config.example.json refresh_config.json  # fill asana_portfolio_gid\n"
            "  3. python3 refresh.py          # full pull rebuilds data_all/\n".format(data_dir))

    # 1. PULL ------------------------------------------------------------------
    if args.skip_pull:
        print("1. PULL: skipped (--skip-pull)")
    else:
        tok = token("ASANA_TOKEN", "asana_token")
        portfolio = cfg.get("asana_portfolio_gid", "")
        if not tok or not portfolio:
            print("1. PULL: skipped -- need ASANA_TOKEN (env or keychain 'asana_token') "
                  "AND asana_portfolio_gid in refresh_config.json. "
                  "Recomputing from the existing pull instead.")
        else:
            # pull into a temp dir; only replace data_all/ inputs on success so
            # a failed/partial pull can't corrupt the current calibration
            tmp = os.path.join(HERE, "data_pull_tmp")
            if not args.dry_run:
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp, exist_ok=True)
            run([py, "asana_pull.py", "pull", "--portfolio", portfolio, "--out", tmp],
                env={"ASANA_TOKEN": tok}, dry=args.dry_run)
            if not args.dry_run:
                required = ["time_entries.csv", "tasks_raw.csv", "projects.csv"]
                missing = [f for f in required if not os.path.exists(os.path.join(tmp, f))]
                if missing:
                    sys.exit(f"pull incomplete (missing {missing}) -- keeping previous data")
                for f in os.listdir(tmp):
                    shutil.move(os.path.join(tmp, f), os.path.join(data_dir, f))
                shutil.rmtree(tmp, ignore_errors=True)

    # 2..5 AUDIT / CALIBRATE / INJECT / BUNDLE ---------------------------------
    run([py, "audit_rules.py", "--dir", data_dir], dry=args.dry_run)
    run([py, "calibrate.py", "--dir", data_dir], dry=args.dry_run)
    run([py, "inject_calibration.py"], dry=args.dry_run)
    run([py, os.path.join("faculty_guide", "build_canvas_bundle.py")], dry=args.dry_run)

    # 6. PUBLISH ----------------------------------------------------------------
    pushed = False
    if args.skip_push:
        print("6. PUBLISH: skipped (--skip-push)")
    elif not cfg.get("canvas", {}).get("course_id"):
        print("6. PUBLISH: skipped -- no canvas config in refresh_config.json "
              "(re-upload canvas_upload/*.html manually, same filename -> Replace)")
    else:
        cmd = [py, "canvas_push.py", "--config", args.config]
        if args.dry_run:
            cmd.append("--dry-run")
        run(cmd)
        pushed = not args.dry_run

    # 7. REPORT -----------------------------------------------------------------
    after = snapshot_numbers(cal_path)
    review_path = os.path.join(data_dir, "derived", "needs_review.csv")
    review = list(csv.DictReader(open(review_path))) if os.path.exists(review_path) else []
    lines = [
        f"ODL estimator refresh -- {datetime.now().isoformat(timespec='seconds')}",
        f"pull: {'skipped' if args.skip_pull else 'ran'} | "
        f"published to Canvas: {'yes' if pushed else 'no'}",
        "", "headline numbers (before -> after):",
    ]
    for k in after:
        b, a = before.get(k), after.get(k)
        mark = "  " if b == a else "->"
        lines.append(f"  {mark} {k}: {b} -> {a}")
    lines.append("")
    if review:
        lines.append(f"ACTION NEEDED -- {len(review)} project(s) await a human decision "
                     "(excluded from calibration until resolved):")
        for r in review:
            lines.append(f"  - {r['name']} ({r['logged_hours']}h): {r['reason'][:140]}")
        lines.append("  Fix: add/update the row in project_registry.csv, then re-run refresh.")
    else:
        lines.append("review queue: empty -- all logged projects classified.")
    report = "\n".join(lines)
    print("\n" + "=" * 70 + "\n" + report)
    if not args.dry_run:
        with open(os.path.join(HERE, "refresh_report.txt"), "w") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
