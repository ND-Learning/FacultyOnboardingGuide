#!/usr/bin/env python3
"""
make_registry.py -- ONE-TIME seeding of project_registry.csv from the verified
2026-06-04 audit (the multi-agent audit whose outputs live in data_all/derived/).

The registry is the human-curated source of truth that makes the pipeline
sustainable: archetype + is_course_dev are judgment calls that a nightly job
must NOT re-guess. Frozen rows carry the audited labels; every NEW project the
pull discovers lands in needs_review.csv until someone adds a row here
(a 2-minute job: gid, archetype, is_course_dev).

Columns:
  gid, name, archetype, is_course_dev, n_videos, n_modules,
  coverage_audited   the audited coverage label (full/partial/minimal)
  audited_through    pull date the label was verified on -- drift detection
                     only considers task completions AFTER this date
  force_coverage     human override: set to "full" to re-include a project
                     that drift-detection demoted (or "exclude" to ban one)
  frozen             1 = labels verified by the 2026-06-04 audit
  notes              evidence / reasoning (from the audit)

Running this again will NOT clobber manual edits: it refuses to overwrite an
existing registry unless --force is passed.
"""
import argparse, csv, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DV = os.path.join(HERE, "data_all", "derived")
OUT = os.path.join(HERE, "project_registry.csv")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if os.path.exists(OUT) and not args.force:
        sys.exit(f"{OUT} already exists -- it may contain manual edits. "
                 "Use --force to regenerate from the audit baseline.")

    rd = lambda p: list(csv.DictReader(open(p, newline="")))
    # seed ONLY from the frozen agent-audit baseline -- never from regenerated
    # files (audit_rules.py output), or demotions would feed back into labels
    BASE = os.path.join(DV, "agent_baseline")
    if not os.path.isdir(BASE):
        sys.exit(f"{BASE} missing -- the verified 2026-06-04 audit baseline is required to seed")
    cov = {r["project_gid"]: r for r in rd(os.path.join(BASE, "audit_coverage.csv"))}
    arch = {r["gid"]: r for r in rd(os.path.join(BASE, "archetypes.csv"))}

    rows = []
    for gid in sorted(set(cov) | set(arch), key=lambda g: -(float(cov.get(g, {}).get("logged_hours") or 0))):
        c, a = cov.get(gid, {}), arch.get(gid, {})
        rows.append({
            "gid": gid,
            "name": (c.get("name") or a.get("name") or "").strip(),
            "archetype": a.get("archetype", ""),
            "is_course_dev": c.get("is_course_dev", ""),
            "n_videos": a.get("n_videos", ""),
            "n_modules": a.get("n_modules", ""),
            "coverage_audited": c.get("coverage", ""),
            "audited_through": "2026-06-03",
            "force_coverage": "",
            "frozen": "1",
            "notes": (a.get("evidence") or c.get("evidence") or "")[:300],
        })

    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {OUT} ({len(rows)} projects, all frozen=1)")
    print("New projects discovered by future pulls will be queued in "
          "data_all/derived/needs_review.csv until added here.")


if __name__ == "__main__":
    main()
