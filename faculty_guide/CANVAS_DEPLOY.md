# Deploying the Faculty Onboarding Guide on Canvas + Pria

## The mental model (important)
**Praxis "Pria" is not a place to host the app — it's a separate AI chat assistant** that
sits in a Canvas course. So "Canvas + Pria" = **two layers**:

1. **The interactive guide** → embedded in a Canvas page (it computes the time/effort estimates).
2. **Pria** → an AI bot, trained on the ODL process docs, that answers faculty's free-text
   questions and points them to the guide. *Pria answers in words; the guide computes the numbers.*

You can do **Layer 1 yourself today, with no admin** (~15 min). **Layer 2 needs OIT/TLT** because
Pria is admin-installed and pilot-gated at ND.

> ⚠️ Deploy the **vanilla** version in this folder (`index.html` + `estimator.js`). Do **not** deploy
> the older React/Gemini prototype (`odl_prototype_extracted/`) — it would ship a Gemini API key in
> the browser bundle. Let **Pria** be the AI, not a key baked into the page.

---

## Access you need first
Canvas roles: **Student → Teacher/Designer → Admin.** A **student** account **cannot** upload Files,
edit Pages, or embed an iframe — you need a **Teacher or Designer** role in the course. Installing
Pria needs **Admin** (OIT). Note: Canvas **no longer issues new "Free-for-Teacher" sandbox accounts**,
so the move is to request a **Designer role in an ODL sandbox course** (that role exists for staff who
build content but don't need admin or student data — i.e., exactly this). Ask Michael/Annie or the
learning designers; at ND it's a quick Technology Service Desk ticket. Until then you can still demo
the guide by opening `index.html` locally, or have a learning designer (who has Teacher access) do
the embed below while you supply the files.

## LAYER 1 — Embed the guide in Canvas (Option 1A: Files + iframe)

This is the recommended path: no admin needed (just **Teacher/Designer** in the course), and the
JavaScript runs because Canvas serves the files from its own (auto-allowlisted) `instructure.com` domain.

> ❌ Do NOT paste the app's HTML into a Canvas Page. The Rich Content Editor **silently strips
> `<script>` tags** on save, so the app saves but never runs. It must run inside an **iframe**.

**Steps**
1. In your Canvas course → **Files** → upload **`canvas_upload/ODL_Faculty_Onboarding_Guide.html`** —
   a single self-contained bundle (JS inlined) built so Canvas's file-serving redirects can't break a
   relative script reference. One file to upload; one file to replace on updates. *(The two-file
   `index.html` + `estimator.js` layout stays for local development; rebuild the bundle after edits.)*
2. Click the uploaded **`.html`** file to preview it. Copy the URL from the address bar — it looks like:
   `https://canvas.nd.edu/courses/<COURSE_ID>/files/<FILE_ID>/download?download_frd=1&verifier=...`
3. Trim everything after the `?` so it ends in `/download?`.
4. Create or edit a **Page** → click the **`</>`** (HTML editor) toggle → paste:

```html
<iframe src="https://canvas.nd.edu/courses/<COURSE_ID>/files/<FILE_ID>/download?"
        width="100%" height="980" style="border:0;"
        title="ODL Faculty Onboarding Guide" allowfullscreen></iframe>
```

5. **Save → Publish → then VIEW the page.** (The iframe shows **blank in Edit mode by design** —
   that's not a bug. It only renders on the published, viewed page.)
6. Test: the type/size picker, "fine-tune deliverables," the computed estimates, and Print/Save-PDF.
   Test once on a phone (Canvas mobile apps sometimes mis-render iframes) — keep the direct file
   link as a mobile fallback.

**To update the guide later — two ways:**
- *Manual:* re-upload the file with the **exact same filename** so Canvas shows the **Replace**
  prompt — this preserves the file URL so your iframe doesn't break. (Renaming creates a new URL
  and breaks the embed.)
- *Automated:* `python3 ../refresh.py` (or the daily `../install_schedule.sh` job) rebuilds the
  bundle from the latest Asana data and `../canvas_push.py` replaces the file via the Canvas API,
  then **self-heals the page iframe** if Canvas assigned a new file id. Needs a Canvas access
  token (Account → Settings → + New Access Token) stored in the keychain as `canvas_token`, and
  the course/page set in `../refresh_config.json`.

> If ODL later wants ONE canonical copy reusable across many courses, host it externally (ND Learning
> WordPress via Ted, or ND GitHub Pages) and iframe that URL instead — **but** that requires a Canvas
> **admin** to add the domain to *Admin → Settings → Security → Content Security Policy → Allowed
> Domains*. The Files route above avoids that entirely.

---

## LAYER 2 — Add Pria as the AI bot

Pria already exists at ND (institution-licensed, "Approved with Qualifications" for a **limited Canvas
pilot**). You don't procure it — you **join the existing pilot**, then **self-train** a course twin.

**Step 1 — Get the ODL course added to the Pria pilot (needs OIT/TLT).**
Email **ai@nd.edu** / OIT Teaching & Learning Technologies (the brief's learning designers can tell you
who already runs Pria courses). Ask: can the Faculty Onboarding Guide ride the **existing** Pria pilot,
or does it need a fresh Innovation Hub review? The install (LTI 1.3 or Custom Theme) is **admin-only**.

**Step 2 — Train a course-specific Pria "digital twin" on ODL content (instructor-level, ~10–20 min).**
Pria has an 8-step Digital Twin wizard. At **Step 7 "Build Knowledge Base,"** upload the ODL process
content (see the `pria_knowledge/` pack I generated, plus the **PM SOPs** and **Charter Template**
PDFs), and/or paste the guide's published URL so Pria scrapes it. Wait for files to read "Ready."

**Step 3 — Set the guardrail.** Configure Pria to answer process/expectation questions and **link to
the embedded guide for any numbers** — the engine computes effort/time; the AI never invents a figure.
(Same wall the guide already enforces.)

---

## Who to contact, and what to ask

| Contact | Ask |
|---|---|
| **Michael & Annie** (your primary stakeholders) | Confirm this vanilla version is canonical; OK to expose the internal effort estimates to all faculty? (decides public vs ND-login-gated) |
| **The 3 ODL learning designers** | Can the guide ride the existing Pria pilot? Embedded page, Pria twin, or both? Who already runs our Pria courses? |
| **Ted** (ND Learning website) | What's involved in hosting one self-contained HTML+JS page on the ND Learning site, and who maintains it? |
| **ai@nd.edu / OIT TLT** | Add an ODL course to the Pria pilot; does the existing approval cover us or is a new review needed? |

### Draft emails (edit and send)

**→ Learning designers**
> Subject: Faculty Onboarding Guide — Canvas embed + Pria pilot?
> Hi [names], I've built an interactive Faculty Onboarding Guide (process walkthrough + time/effort
> estimator, grounded in our project data). I'd like to put it in Canvas with Pria as the AI bot.
> Two questions: (1) can it ride our **existing** Pria pilot, and (2) would you recommend it as an
> embedded Canvas page, a Pria-trained twin, or both? Happy to demo — it's a single static page I can
> drop into a course in ~15 min. Thanks, Juntong

**→ Ted (website track, in parallel)**
> Subject: Hosting a single interactive HTML page on the ND Learning site
> Hi Ted, I have a self-contained HTML+JS page (no backend) for a Faculty Onboarding Guide. If we
> wanted it on the ND Learning website (vs. embedded in Canvas), what would that take, who maintains
> it, and could it be public or must it be login-gated? Thanks, Juntong

**→ ai@nd.edu / OIT TLT**
> Subject: Adding an ODL course to the Praxis Pria pilot
> Hi, ODL has built a Faculty Onboarding Guide and would like to use Pria as the AI assistant,
> grounded on our process docs. Since Pria is already approved for the limited Canvas pilot, can an
> ODL course be added, and does our use fall under the existing approval or need a new Innovation Hub
> review? Thanks, Juntong (ODL PM intern)

---

## Top traps
- **RCE strips `<script>`** — never paste the app into a Page; always iframe.
- **External host = admin allowlist** — GitHub Pages/Netlify/ND site as the iframe source needs a
  Canvas admin to allowlist the domain (CSP). The Files route sidesteps this.
- **Pria is admin-installed + pilot-gated** — joining the pilot is the real gate, not licensing.
- **Deploy the vanilla version**, not the React/Gemini prototype (API-key exposure).
- **Same-filename replace** to update without breaking the iframe.
- **Accessibility + ND branding** must pass before a faculty-facing go-live.
