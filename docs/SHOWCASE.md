# Public showcase site (static replay): export, build, gate, deploy

The public site opens on a landing page (`web/landing.html`, served as `/`): results with sources, the pipeline, the
rig and the roadmap, around a drawing of the lab twin's measured points (`tools/export_landing_points.py` writes
`web/src/landing/twin-points.*` from a twin; `node tools/capture-landing.mjs <preview url>` refreshes its studio and
link-preview images). One click opens `/replay`: the real studio replaying a recorded session from static files. A
first visit therefore downloads under 1 MB; the replay data (about 22 MB for 30 s) loads only on request. The site
has no backend, never connects to the rig, and says on every screen that it is a **recorded replay, not live**.
`web/tests/landing.test.ts` fails if a figure on the landing page stops matching its source file. Nothing is deployed
without the owner's explicit yes, and every deploy passes the gate in step 3 first.

## 1. Export a recorded session (laptop, about 3 minutes per 30 s on the GPU)

```powershell
.\.venv-gpu\Scripts\python.exe tools\export_showcase.py --recording <raw recording dir> --twin <twin dir> `
  --config config\profiles\hotspot.yaml --seconds 30 --output $env:USERPROFILE\aethertwin_demo\showcase_<name> --device cuda
```

Add `--calibration <json>` for a calibrated twin, or `--lidar-only --lidar-height-assumed` for the bench twin. The
exporter runs the real parked pipeline offline and writes the following, all to SHOWCASE FORMAT v1:
- `index.json`;
- `bootstrap.json`, read-only;
- `chunks/NNNN-<hash>.bin.gz`, each a gzip of protocol-v2 packets that starts with a keyframe;
- `assets/<revision>/`, the map tiles, whose URLs are relative to `manifest.json`.

Diagnostics go through an allowlist. A byte scan of every output file, decompressed chunks included, fails the
export on IPs, user paths, device identifiers and long hex strings. Measured: 60 s of the bench recording = 716
frames, 33.5 MB.

## 2. Build the static site

```powershell
cd web
$env:SHOWCASE_DATA_DIR="$env:USERPROFILE\aethertwin_demo\showcase_<name>"; npm run build:showcase
npm run preview:showcase   # http://127.0.0.1:4180
```

`showcase-dist/` is git-ignored. The build has:
- no sourcemaps;
- no `/api` or `/ws` strings;
- a permanent provenance banner;
- replay connection state.

Data can be hosted elsewhere: build with `VITE_SHOWCASE_BASE=https://<cdn>/showcase/`.

End-to-end check: `npm run e2e:showcase` builds `showcase-dist/` with the committed fixture export in
`web/e2e/showcase/fixture` (overwriting any earlier build) and serves it on port 4185 (`E2E_SHOWCASE_PORT`). The test checks:
- the landing page: under 1 MB and 15 requests, no replay data or 3D libraries, phone width, axe;
- the banner and its provenance;
- the "Recorded replay" state;
- playback, pause, step and seek;
- the explainer link;
- axe;
- no `/api`, `/ws` or WebSocket traffic and no console errors.

The fixture is a 7-frame TEST EXPORT of `assets/sample_recording`. To regenerate it, empty the folder and run
`tools\export_showcase.py --recording assets\sample_recording --twin assets\sample_recording\twin --config config\system_config.yaml --allow-synthetic --seconds 0.7 --chunk-seconds 0.25 --device cpu --output web\e2e\showcase\fixture`.

## 3. Gate (mandatory before any deploy)

```powershell
.venv\Scripts\python.exe tools\predeploy_check.py web\showcase-dist --vercel-json web\vercel.json --visitors-per-month 300 --views-per-visit 2
```

The gate checks:
- a leak scan, `.gz` files included;
- the Vercel Hobby size limits;
- the CSP, security headers and caching in `web/vercel.json`;
- dangling references;
- 404 behaviour;
- a local load simulation, projected against the team's 1,000,000 edge requests and 100 GB a month.

Any FAIL means: do not deploy.

## 4. Hosting decision (owner)

A full replay is heavy. Each view fetches every chunk plus the map tiles: about 300 requests and up to 35 MB for
60 s. On Vercel Hobby, about 1,400 visitors a month at 2 views each would exhaust the shared limits, and the team
has already used its one courtesy unblock.

| Option | Hobby impact | Effort |
|---|---|---|
| **A.** Site on Vercel, replay data on Cloudflare R2 (free egress) via `VITE_SHOWCASE_BASE` | small (code ~0.3 MB gz a view) | an R2 bucket + CORS rule (owner's account) |
| **B.** Whole site on Cloudflare Pages | none | owner's Cloudflare account; 25 MiB per file is fine |
| **C.** Vercel only, a short loop (10-15 s) | still large per view | none |

Before any deploy of any project, the global rules in `~/.claude/CLAUDE.md` ("Vercel deployments: abuse-proofing
gate") apply:
- turn on bot protection;
- add the one rate-limit rule Hobby allows;
- keep Deployment Protection on previews;
- keep a deny-all kill switch ready.

As of 2026-11-05 every Vercel project of the team is disabled with a deny-all firewall rule. A new project starts
open until those rules are added.

## 5. Deploy (only on the owner's yes, after the gate passes)

The site is uploaded as static files; nothing is built on Vercel. Project: `aethertwin-showcase`.

1. Read the project's active firewall rules and confirm the 300/min per-IP rate limit and bot protection are on, and
   that the team is not blocked.
2. Stage it (runs the step 3 gate, refuses the e2e TEST EXPORT fixture, never writes into a non-empty folder):

   ```powershell
   .venv\Scripts\python.exe tools\stage_showcase.py --previous <last staging folder>
   ```

   It copies `web/showcase-dist/` into `%USERPROFILE%\aethertwin_demo\vercel_showcase_<YYYYMMDD>`, copies `.vercel/` and
   `.gitignore` from `--previous` (the link to the existing project), and writes `vercel.json` from `web/vercel.json`
   with `outputDirectory` removed and `buildCommand` and `installCommand` set to `""`. Tests:
   `tests/test_stage_showcase.py`.
3. In the folder it prints: `vercel deploy --prod --yes`.
4. Check the live site in a real browser. `curl` gets 429 from the bot challenge, which is expected. Check: the landing
   page loads without 3D libraries or replay data, `/replay` plays, `/explainer.html?x=1` keeps its query, an unknown
   path gives 404.

Last deploy: 21 Nov 2026 13:59 UTC, commit `59c4797` (PR #10: ledger overlap fix, "Built with" strip, 700+ / 713
tests, new `og.jpg`), replay from `showcase_lab_2026-11-05` (360 frames, 30 s), staged in `vercel_showcase_20261121`.
Gate: pass=10 warn=2 fail=0. Firewall read through the API just before: 300/min per-IP rate limit and bot protection
(challenge) active, team not blocked. Checked live in headed Chrome at 1280 px: landing 9 requests with no replay data or
3D libraries, ledger values clear of the next column, `/replay` plays (48 to 96 of 360 frames in 3 s), the explainer
keeps `?x=1`, an unknown path is 404.

Previous deploy: 21 Nov 2026, commit `93c6445` (landing page at `/`, replay at `/replay`). Landing page measured live at
9 requests and 178 kB transferred.
