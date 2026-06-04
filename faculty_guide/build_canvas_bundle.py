#!/usr/bin/env python3
"""Rebuild the single-file Canvas bundle after editing index.html / estimator.js.
Run:  python3 build_canvas_bundle.py
Then re-upload canvas_upload/ODL_Faculty_Onboarding_Guide.html to Canvas Files
with the SAME filename so Canvas offers 'Replace' (keeps the URL -> iframe intact)."""
import os
here = os.path.dirname(os.path.abspath(__file__))
html = open(os.path.join(here, "index.html")).read()
js = open(os.path.join(here, "estimator.js")).read()
tag = '<script src="estimator.js"></script>'
assert tag in html, "index.html no longer references estimator.js the expected way"
bundled = html.replace(tag, "<script>\n" + js + "\n</script>")
out = os.path.join(here, "canvas_upload", "ODL_Faculty_Onboarding_Guide.html")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "w").write(bundled)
print("rebuilt %s (%d chars)" % (out, len(bundled)))
