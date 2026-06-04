#!/usr/bin/env python3
"""
inject_calibration.py -- regenerate the machine-generated CALIBRATION block in
faculty_guide/estimator.js from data_all/derived/calibration.json.

This is the wall against transcription errors: numbers flow
  Asana -> time_entries.csv -> calibrate.py -> calibration.json -> (this) -> estimator.js
and are never typed by hand. Re-run after every calibrate.py run:

  python3 calibrate.py --dir data_all && python3 inject_calibration.py
"""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
CAL = os.path.join(HERE, "data_all", "derived", "calibration.json")
EST = os.path.join(HERE, "faculty_guide", "estimator.js")

BEGIN = "  // === BEGIN CALIBRATION (machine-generated — do not edit by hand) =========="
END = "  // === END CALIBRATION ======================================================="


def main():
    cal = json.load(open(CAL))

    def q(block):  # pass through as-is (n<4 blocks carry raw "values" -- keep them)
        return block or None

    payload = {
        "provenance": cal["_provenance"]["source"]
                      + " | " + cal["_provenance"]["calibration_set"],
        "generated_from": "data_all/derived/calibration.json -- run inject_calibration.py to refresh",
        "archetypeEffort": {a: q(b["production_hours"])
                            for a, b in cal["archetype_effort_hours"].items()},
        "pmPerWeek": q(cal["pm_model"]["pm_hours_per_week"]),
        "pmSharePctWhereBothLogged": q(cal["pm_model"]["pm_share_pct"]),
        "videoRate": {
            "observed": q(cal["video_unit_rates"]["observed_quartiles"]),
            "blendedGenericP50": cal["video_unit_rates"]["blended_generic_video_p50"]["value"],
            "shrinkageW": cal["video_unit_rates"]["blended_generic_video_p50"]["shrinkage_w"],
        },
        "mediaSplitPct": cal["media_lifecycle_split"]["split_pct"],
        "mediaSplitBasis": cal["media_lifecycle_split"]["basis"],
        "coursePhaseMixPct": cal["full_course_phase_mix"]["share_pct"],
        "coursePhaseMixBasis": cal["full_course_phase_mix"]["basis"],
        "calendarWeeks": {a: q(b) for a, b in
                          cal["calendar"]["span_weeks_by_archetype"].items()},
        "notCalibratable": cal["not_calibratable"],
        "backtest": {k: cal["backtest"][k]
                     for k in ("median_ape_pct", "iqr_coverage_pct", "reading")},
    }

    js = json.dumps(payload, indent=2, ensure_ascii=False)
    js = "\n".join("  " + line for line in js.splitlines())
    block = f"{BEGIN}\n  var CALIBRATION =\n{js};\n{END}"

    src = open(EST, encoding="utf-8").read()
    pat = re.compile(re.escape(BEGIN) + r".*?" + re.escape(END), re.S)
    if not pat.search(src):
        sys.exit("ERROR: CALIBRATION markers not found in estimator.js")
    open(EST, "w", encoding="utf-8").write(pat.sub(lambda _: block, src))
    print(f"injected CALIBRATION block into {EST}")
    print(f"  archetypes: {list(payload['archetypeEffort'])}")
    print(f"  pm p25-p75: {payload['pmPerWeek']['p25']}-{payload['pmPerWeek']['p75']} h/wk")


if __name__ == "__main__":
    main()
