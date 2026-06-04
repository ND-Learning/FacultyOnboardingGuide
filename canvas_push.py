#!/usr/bin/env python3
"""
canvas_push.py -- publish the rebuilt bundle to Canvas via the official API:

  1. upload canvas_upload/ODL_Faculty_Onboarding_Guide.html into the course's
     Files (3-step Canvas upload flow), on_duplicate=overwrite
  2. read the embed Page, rewrite the iframe's /files/<id>/download URL to the
     (possibly new) file id, and PUT the page back

Step 2 makes the embed self-healing: even if Canvas assigns a new file id on
overwrite, the page is updated in the same run, so the iframe never breaks.

Auth: CANVAS_TOKEN env var, or macOS keychain item 'canvas_token'
      (Canvas -> Account -> Settings -> + New Access Token).

Config (refresh_config.json):
  "canvas": {
    "base_url": "https://canvas.nd.edu",
    "course_id": "148587",
    "page_url": "plan-your-project-with-odl",   <- the page slug from its URL
    "parent_folder_path": "Uploaded Media",
    "file_name": "ODL_Faculty_Onboarding_Guide.html"
  }

Usage:  python3 canvas_push.py --config refresh_config.json [--dry-run]
Stdlib only (urllib) -- no pip installs to keep the scheduled job dependency-free.
"""
import argparse, json, mimetypes, os, re, subprocess, sys, urllib.parse, urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))


def keychain(service):
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", service, "-w"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def api(base, tok, method, path, data=None, raw_url=None):
    url = raw_url or (base.rstrip("/") + "/api/v1" + path)
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode() or "{}")


def multipart_post(url, fields, file_field, file_name, file_bytes):
    """POST multipart/form-data with stdlib only (Canvas step-2 upload)."""
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n'.encode())
    ctype = mimetypes.guess_type(file_name)[0] or "text/html"
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; "
                 f'name="{file_field}"; filename="{file_name}"\r\n'
                 f"Content-Type: {ctype}\r\n\r\n".encode())
    parts.append(file_bytes)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as r:
        # Canvas replies 201 with a Location to confirm, or the file JSON directly
        loc = r.headers.get("Location")
        payload = r.read().decode() or "{}"
    return loc, json.loads(payload)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "refresh_config.json"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config))["canvas"]
    base, course = cfg["base_url"], str(cfg["course_id"])
    fname = cfg.get("file_name", "ODL_Faculty_Onboarding_Guide.html")
    fpath = os.path.join(HERE, "faculty_guide", "canvas_upload", fname)
    if not os.path.exists(fpath):
        sys.exit(f"bundle not found: {fpath} -- run build_canvas_bundle.py first")
    tok = os.environ.get("CANVAS_TOKEN", "").strip() or keychain("canvas_token")
    if not tok:
        sys.exit("no CANVAS_TOKEN (env) and no 'canvas_token' keychain item -- "
                 "create one: Canvas -> Account -> Settings -> + New Access Token")

    size = os.path.getsize(fpath)
    print(f"publishing {fname} ({size} bytes) -> course {course} on {base}")
    if args.dry_run:
        print("dry-run: would upload file (on_duplicate=overwrite), then verify/fix "
              f"iframe on page '{cfg.get('page_url','(none)')}'. No changes made.")
        return

    # -- step 1-3: Canvas file upload flow ------------------------------------- #
    pre = api(base, tok, "POST", f"/courses/{course}/files", {
        "name": fname, "size": size,
        "parent_folder_path": cfg.get("parent_folder_path", "Uploaded Media"),
        "on_duplicate": "overwrite",
    })
    upload_params = pre["upload_params"]
    loc, payload = multipart_post(pre["upload_url"], upload_params, "file",
                                  fname, open(fpath, "rb").read())
    if loc:  # confirmation redirect carries the file JSON
        payload = api(base, tok, "POST", "", raw_url=loc)
    file_id = payload.get("id") or payload.get("attachment", {}).get("id")
    if not file_id:
        sys.exit(f"upload succeeded but no file id in response: {payload}")
    print(f"uploaded: file id {file_id}")

    # -- step 4: self-heal the page iframe -------------------------------------- #
    page_url = cfg.get("page_url", "").strip()
    if not page_url:
        print("no page_url configured -- skipping iframe check (file replaced only)")
        return
    page = api(base, tok, "GET", f"/courses/{course}/pages/{urllib.parse.quote(page_url)}")
    body = page.get("body") or ""
    pat = re.compile(r"(/courses/%s/files/)(\d+)(/download)" % course)
    m = pat.search(body)
    if not m:
        print(f"WARN: page '{page_url}' has no /courses/{course}/files/<id>/download "
              "iframe -- nothing to update. Embed it once by hand first.")
        return
    if m.group(2) == str(file_id):
        print(f"page iframe already points at file {file_id} -- no page edit needed")
        return
    new_body = pat.sub(lambda mm: mm.group(1) + str(file_id) + mm.group(3), body)
    api(base, tok, "PUT", f"/courses/{course}/pages/{urllib.parse.quote(page_url)}",
        {"wiki_page[body]": new_body})
    print(f"page '{page_url}' iframe updated: file {m.group(2)} -> {file_id}")


if __name__ == "__main__":
    main()
