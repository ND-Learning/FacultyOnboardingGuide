#!/usr/bin/env python3
"""
package_handoff.py -- build a clean, runnable handoff zip of the estimator
project, safe to archive (e.g., upload to Canvas Files for continuity, hand to
a successor, or push to GitHub as the initial commit).

Two modes:
  python3 package_handoff.py             # CODE+DOCS zip (default, PII-safe):
                                         #   scripts, docs, registry, workflows,
                                         #   faculty_guide. NO raw time entries
                                         #   (staff names/hours stay internal).
                                         #   A fresh `refresh.py` pull rebuilds
                                         #   data_all/ from Asana.
  python3 package_handoff.py --with-data # adds data_all/ (incl. per-person
                                         #   entries) -- ODL-INTERNAL ONLY,
                                         #   never a faculty-visible course.

Always excluded: refresh_config.json (machine-local), logs, temp dirs, and a
token scan refuses to package if anything resembling a live token is found.
Output: dist/ODL_estimator_handoff[_with_data]_<date>.zip + a MANIFEST.
"""
import argparse, os, re, sys, zipfile
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))

INCLUDE = [
    "README.md", "CALIBRATION.md", "HANDOFF.md",
    "project_registry.csv", "refresh_config.example.json",
    "asana_pull.py", "audit_rules.py", "calibrate.py", "inject_calibration.py",
    "make_registry.py", "refresh.py", "canvas_push.py", "package_handoff.py",
    "install_schedule.sh", "odl_media_unit_rates.csv",
    ".github/workflows/refresh.yml",
    "faculty_guide/index.html", "faculty_guide/estimator.js",
    "faculty_guide/build_canvas_bundle.py", "faculty_guide/README.md",
    "faculty_guide/CANVAS_DEPLOY.md",
    "faculty_guide/canvas_upload/ODL_Faculty_Onboarding_Guide.html",
]
DATA_DIRS = ["data_all"]
# charts regenerate; logs excluded. agent_baseline/ IS shipped in the with-data
# zip -- docs declare it a frozen invariant, so the archive must carry it.
DATA_EXCLUDE_RE = re.compile(r"(^|/)charts(/|$)|\.log$")

# refuse to ship anything that looks like a live credential
TOKEN_RES = [
    re.compile(r"\b\d/\d{10,}/\d{10,}:[0-9a-f]{16,}\b"),       # asana PAT shape
    re.compile(r"\b1[0-9]{3,4}~[A-Za-z0-9]{40,}\b"),           # canvas token shape
    re.compile(r"(api[_-]?key|secret)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}", re.I),
]


def token_scan(path):
    try:
        text = open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return None
    for rx in TOKEN_RES:
        m = rx.search(text)
        if m:
            return f"{path}: looks like a live credential ({m.group(0)[:14]}…)"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-data", action="store_true",
                    help="include data_all/ (per-person entries) -- ODL-internal only")
    args = ap.parse_args()

    files = []
    for rel in INCLUDE:
        p = os.path.join(HERE, rel)
        if os.path.exists(p):
            files.append(rel)
        else:
            print(f"  (skipping missing {rel})")
    if args.with_data:
        for d in DATA_DIRS:
            for root, _, names in os.walk(os.path.join(HERE, d)):
                for n in names:
                    rel = os.path.relpath(os.path.join(root, n), HERE)
                    if not DATA_EXCLUDE_RE.search(rel.replace(os.sep, "/")):
                        files.append(rel)
        # the audited baseline + charts regenerate; keep zip lean but DO keep
        # derived CSVs so the zip runs offline with --skip-pull
        for keep in ("data_all/derived/calibration.json",):
            if os.path.exists(os.path.join(HERE, keep)) and keep not in files:
                files.append(keep)

    problems = [w for w in (token_scan(os.path.join(HERE, f)) for f in files
                            if f.endswith((".py", ".md", ".json", ".sh", ".yml",
                                           ".html", ".js", ".csv"))) if w]
    if problems:
        print("REFUSING to package -- possible live credentials found:")
        for p in problems:
            print("  -", p)
        sys.exit(1)

    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    tag = "_with_data" if args.with_data else ""
    out = os.path.join(HERE, "dist",
                       f"ODL_estimator_handoff{tag}_{date.today().isoformat()}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in sorted(set(files)):
            z.write(os.path.join(HERE, rel), arcname=f"odl_estimator/{rel}")
        manifest = "\n".join(sorted(set(files)))
        z.writestr("odl_estimator/MANIFEST.txt",
                   f"ODL estimator handoff -- {date.today().isoformat()}\n"
                   f"mode: {'WITH raw data (ODL-internal only)' if args.with_data else 'code+docs (PII-safe)'}\n"
                   f"token scan: clean\nfiles:\n{manifest}\n")
    mb = os.path.getsize(out) / 1e6
    print(f"\nwrote {out} ({mb:.1f} MB, {len(set(files))} files)")
    if not args.with_data:
        print("PII-safe: contains no time entries. First run on a new machine: "
              "set tokens, then `python3 refresh.py` (full pull rebuilds data_all/).")
    else:
        print("CONTAINS PER-PERSON TIME DATA -- share only inside ODL; never "
              "upload to a faculty-visible Canvas course.")


if __name__ == "__main__":
    main()
