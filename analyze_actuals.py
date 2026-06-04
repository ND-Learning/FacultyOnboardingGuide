#!/usr/bin/env python3
"""Quick grounding analysis of the 42-project pull: real tracked hours per
project, calendar spans, and where logged time falls across phases. Output
feeds the faculty guide's estimate model so the numbers are ODL-grounded."""
import csv, json, re
from collections import defaultdict
from datetime import datetime

D = "data_archived/"

def to_hours(s):
    if not s: return None
    h=re.search(r"(\d+)\s*h", s); m=re.search(r"(\d+)\s*m", s)
    if not h and not m: return None
    return (int(h.group(1)) if h else 0)+(int(m.group(1)) if m else 0)/60.0

def dt(s):
    if not s: return None
    try: return datetime.fromisoformat(s.replace("Z","+00:00"))
    except ValueError: return None

# keyword -> canonical phase (rough, for grounding only)
def phase_of(sec):
    s=(sec or "").lower()
    if any(k in s for k in ["intake","analysis","kickoff","charter","discovery","planning"]): return "Discovery"
    if any(k in s for k in ["pre-prod","pre prod","script","storyboard"]): return "Pre-Production"
    if any(k in s for k in ["post-prod","post prod","edit"]): return "Post-Production"
    if any(k in s for k in ["production","film","shoot","record"]): return "Production"
    if any(k in s for k in ["media","video","animation","graphic"]): return "Production"
    if any(k in s for k in ["design","map","content","build","develop","lms","assessment"]): return "Design"
    if any(k in s for k in ["qa","launch","review","quality"]): return "QA & Launch"
    if any(k in s for k in ["eval","retro","survey","reflection","handoff","delivery"]): return "Evaluation"
    return "Other"

tasks=list(csv.DictReader(open(D+"tasks_raw.csv", newline="")))
projs={r["project_gid"]:r for r in csv.DictReader(open(D+"projects.csv", newline=""))}

proj_hours=defaultdict(float); phase_hours=defaultdict(float)
proj_media=defaultdict(int)
for t in tasks:
    tt=float(t.get("tracked_minutes") or 0)/60.0
    proj_hours[t["project_gid"]]+=tt
    phase_hours[phase_of(t["section"])]+=tt

print("=== PROJECTS WITH LOGGED HOURS (sorted) ===")
rows=[]
for gid,h in sorted(proj_hours.items(), key=lambda kv:-kv[1]):
    if h<=0: continue
    p=projs.get(gid,{})
    span=p.get("span_days_completed") or ""
    rows.append((p.get("project_name","?").strip(), round(h,1), span))
for name,h,span in rows:
    print(f"  {h:7.1f} h   span={span:>5}d   {name}")
print(f"\n  {len(rows)} projects with hours; total tracked = {sum(r[1] for r in rows):.0f} h")
print(f"  median project = {sorted(r[1] for r in rows)[len(rows)//2]:.1f} h")

print("\n=== WHERE LOGGED TIME FALLS (phase distribution of tracked hours) ===")
tot=sum(phase_hours.values()) or 1
for ph,h in sorted(phase_hours.items(), key=lambda kv:-kv[1]):
    print(f"  {ph:16} {h:7.1f} h   {100*h/tot:5.1f}%")

print("\n=== CALENDAR SPANS (clean, where available) ===")
spans=[]
for gid,p in projs.items():
    s=p.get("span_days_completed")
    try: s=int(s)
    except (ValueError,TypeError): continue
    if 7<=s<=600: spans.append(s)
if spans:
    spans.sort()
    print(f"  n={len(spans)}  min={spans[0]}d  median={spans[len(spans)//2]}d  "
          f"max={spans[-1]}d  ({spans[len(spans)//2]/7:.0f} wk median)")
