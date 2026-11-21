# AetherTwin — working notes for Claude

**Next phase (operator laptop + physical rig): read `NEXT_AGENT.md` first.** It is the rulebook;
`docs/LAB_DAY.md` is the step list (done and remaining), `docs/DEMO.md` the demo runbook (live / recorded / sim tiers),
`docs/SHOWCASE.md` the public static replay site (export -> build:showcase -> `tools/predeploy_check.py` gate -> deploy
only on the owner's yes) and `docs/HARDWARE_SETUP.md` the bench guide for the human. `HANDOFF.md` is history from the
improvement pass. Keep `PROGRESS.md` local/untracked.

Python 3.12 research platform (LiDAR/RGB-D perception, mapping, tracking, planning) plus a
React 19 / TypeScript 7 / Vite 8 / Three.js r186 browser studio in `web/`. Honesty about
evidence is a core convention: UI and docs never imply hardware verification or accuracy
that was not measured; visual effects are presentation-only and must say so.

## Layout
- `command_center/` FastAPI service (`service.py`), auth, controls, binary scene protocol v2, gateway.
- `ingestion/` sensor drivers, sync, recorder; raw-sensor transport: `raw_transport.py` (protocol),
  `capture_node.py` (Pi server), `remote_source.py` (Windows receiver, `live.source: remote`).
  `self_mask.py`: `lidar_self_mask` boxes (LiDAR frame) drop the robot's own body where frames enter
  `LabPipeline.process` and the commissioning build; recordings stay raw. Off until measured on the rig.
  `tools/suggest_self_mask.py` proposes boxes from a parked recording (suggestion only; never edits config).
- `web/src/` studio: `App.tsx`, `components/`, `lib/` (api, status, types), `scene/` (Three.js
  `SpatialRenderer`, protocol decoder, worker). `web/dist/` is COMMITTED and served by Python.
- `web/src/explainer/` explainer page (`explainer.html`): content in `content.ts` (every number cites a
  repo source; a test enforces it), 2D labs ported from the Python constants in `math.ts`.
- `web/landing.html` + `web/src/landing/` showcase landing page (plain HTML/CSS/TS, no React; served as `/` of the
  showcase build, the studio replay as `/replay`). Every figure carries `data-source`/`data-evidence`; a test enforces it.
  Summary docs: `docs/CASE_STUDY.md`, `docs/FUTURE_WORK.md`, `docs/RESUME_ENTRY.md`.
- Photoreal: `web/src/scene/photoreal.ts` (lazy quality tier, presentation only) and the visual-only photo
  capture layer (`command_center/appearance.py`, `--appearance DIR`, `docs/LAB_APPEARANCE.md`).
- `web/tests/` Vitest unit/contract tests; `web/e2e/` Playwright (`mock/`, `live/`, `support/`).
- `tests/` pytest; `tools/` scripts; `docs/` architecture/status; `reports/`, `artifacts/` evidence.

## Commands (keep output lean: pipe through `tail`/`grep`)
- Python env: `uv venv -p 3.12 .venv && uv pip install --python .venv/bin/python --index-strategy unsafe-best-match --require-hashes -r requirements-ubuntu.lock`
- Python tests: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -5` (~5 min on Windows; 719 pass, 11 skip on Linux as root on 21 Nov, `reports/test_suite/README.md`)
- Fast pre-flight: `python tools/fast_check.py` (lab-critical tests, ~1 min); parallel full suite: `python tools/fast_check.py --full`
  (no pytest-xdist in the lock; it runs several pytest processes and prints `passed=… total=…`).
- **CI minutes are the free tier** (2,000/month, Windows bills 2x): the gate is local. Before a push run
  `python tools/local_ci.py` (the ci.yml stages the changed files need; `--linux` adds the Ubuntu suite in WSL, ~80 s
  after a one-time ~3 min venv build in `~/aethertwin-ci`; `--all --linux --e2e` before a merge). Batch commits,
  push rarely. Remote CI skips draft PRs, runs Ubuntu only on ready PRs, and adds Windows only on main or
  `gh workflow run ci.yml --ref <branch>` (use only when a remote check is really needed).
- AIDE² v2 (`aide2-harness` skill; boot with it every session): journal `docs/aide2_tree.jsonl` via
  `python ~/.claude/skills/aide2-harness/scripts/aide2_cycle.py begin|guard|record|status|reflect`. RED test first
  (show RED on a `git worktree add --detach <dir> HEAD` copy when other agents share this checkout); exact speed-ups
  carry an oracle copy of the old code. Record PUBLIC (the cycle's tests) and PRIVATE scores: here the private check
  is `tools/fast_check.py --full` plus, for live-path changes, a 10-minute `tools/rehearse_live.py` run compared with
  `reports/rehearsal/` (pass `--metric p50_ms=<v> --baseline p50_ms=<b> --goal p50_ms=min --noise p50_ms=0.05`).
  Lessons: hardware/data/stack facts are `--lesson-scope project`; method, tooling, CI/deploy rules are `global`.
- Real recordings for offline checks: `%USERPROFILE%/aethertwin_rec/wifi_stationary_60s` (716 sweeps) and `_600s`
  (5.6 GB, never in git). Build: `main.py --mode build-twin --config config/profiles/hotspot_build.yaml ...` (4 sweeps
  per scan + auto IMU bias; one sweep per scan does not register on the L2).
- Windows: `.venv\Scripts\python.exe` instead of `.venv/bin/python`; install with plain
  `pip install --require-hashes -r requirements-windows.lock`.
- Web (in `web/`): `npm ci`, `npm run typecheck`, `npm test`, `npm run build` (rebuilds `dist/`; must leave git clean if source unchanged)
- E2E mock (needs fresh `npm run build`): `npx playwright test --project=mock --reporter=line 2>&1 | tail -15`
- E2E live: start backend `setsid .venv/bin/python -m command_center --mode sim --threads 2 --output /tmp/at &`,
  then `AETHERTWIN_SERVICE_URL=http://127.0.0.1:8080 npx playwright test --project=live --reporter=line`
- Manifest (CI checks it): after staging, `python3 tools/repository_manifest.py --index && git add SOURCE_MANIFEST.json`
  (`--index` hashes the staged content, so unstaged edits of other agents do not matter; without it, stash them first)
- Bundle budget: `npm run size` after a build. GPU benchmark: `node tools/run-benchmark.mjs --mode streaming
  --quality balanced --out ../artifacts/renderer_benchmarks/x.json` (headed Chrome, real GPU).
- Cloud GPU (Modal): `modal run tools/modal_bench.py --suite smoke --gpu T4` (see reports/modal/README.md).
- Poster PDF (Windows, installed Chrome): in `web/`, `node tools/export-poster.mjs` rewrites
  `reports/poster/AetherTwin_poster_A3.pdf` from `poster_a3.html`; it refuses if text runs past the page or title box.
- Public repo `aethertwin-lab` is a curated copy, not a mirror: sync steps in the vault note "Public repo aethertwin-lab
  is a curated copy" (script `.local/public_sync/curate.py`, local only). Show the owner the file list before a push.

## Gotchas
- `vite preview` proxies `/api` and `/ws` to :8080. Mock E2E relies on the AUTO `service` fixture
  (`e2e/support/test.ts`); a non-auto fixture silently lets tests hit a real local backend.
- Mock E2E serves `web/dist`, so rebuild before running it after source edits.
- `@axe-core/playwright` is pinned to our `playwright-core` via npm `overrides`.
- `uv` needs `--index-strategy unsafe-best-match` for the lock (pip semantics across two indexes).
- Headless WebGL needs `--use-angle=swiftshader --enable-unsafe-swiftshader` (set in playwright.config.ts).
- `pkill -f <pattern>` kills your own shell when the pattern is in the command line; stop the service by PID.
- Several agent sessions may share this laptop. Rehearse the live run ONLY with `tools/rehearse_live.py` (machine lock, fresh
  key, free ports, new folder under `%USERPROFILE%/aethertwin_rehearsals`, PID-tree cleanup). Never stop processes by
  command-line pattern or "find the replay node" searches: on 2026-11-05 that killed another session's rehearsal, and two
  rehearsals on fixed ports with one key took over each other's node. A venv `python.exe` on Windows is a launcher stub:
  its child is the real interpreter (memory, kill the tree).
- The live route E2E derives endpoints from `/api/v2/state` because the simulated robot reaches its goal.
- Python style in this repo is dense (semicolon-joined statements); match surrounding code.
- Some files use CRLF (e.g. `slam_and_twin/log_odds_octomap.py`) and `.gitattributes` keeps exact bytes: edit
  bytes-preserving (Python `read_text` converts CRLF to LF and rewrites every line). Check with `grep -c $'\r$' FILE`.
- SOURCE_MANIFEST.json hashes every tracked file; any tracked change requires regenerating it.
- Local Playwright runs cap workers at 4 (`E2E_WORKERS`); more SwiftShader contexts time out.
- Stray `vite preview` processes share port 4173 on Windows and serve stale chunk lists (random 404s and
  "never connected" failures); dead servers can leave orphan listeners (PID 0). Kill strays by PID or set
  `E2E_PREVIEW_PORT` to a free port; services likewise accept `--port`.
- In `cmd`, call a `.cmd` from another with `call`, or nothing after it runs.
- Python 3.12 on Windows: `time.monotonic()` ticks in 15.6 ms steps (GetTickCount64). Use
  `ingestion.raw_transport.host_monotonic()` where sub-millisecond host times matter.

## Remaining plan (update as tasks complete)
1. Playwright E2E (mock + live + axe) — done (`3222c84`).
2. Refactor `App.tsx` into hooks/components — done (`af0a6b2`).
3. Renderer perf, cinematic effects, camera animation — done (`a1e681d`, `fc92cdb`).
4. UX: shortcuts, help overlay, quality selector, toast, map progress — done (`00d8efe`).
5. Modal cloud GPU benchmarks + CUDA lock — done (`96c62a4`).
6. CI/CD — done (`c0d8fff`). IoT gateway + halo hardening — done (`29ed107`).
7. `docs/REVIEW_AND_ROADMAP.md` (backlog lives there), security review, PR #1 merged to main.
8. Raw-sensor transport and device bring-up (`hw/raw-transport`): loopback soak; Pi, L2, Gemini 336L
   (USB 2, 640x400) and ESP32-S3 serial link brought up; first 60 s Pi-to-laptop stream over Wi-Fi
   (`9c96ae2`, `reports/hardware/`). LEDs, wired transport and long/moving recordings not done.
   Overnight 5 Nov (`3879fd3`..`8975693`): the real bench recordings through build-twin and a 30-minute parked live
   rehearsal (`reports/rehearsal/`); fixes and new tools listed in `docs/LAB_DAY.md` "Overnight software changes".
9. Lab 5 Nov afternoon (`reports/lab/README.md`): cart mount, empty-room recording, self-mask (no box), mounted IMU
   bias, a twin from the stationary recording, and a 15-minute LiDAR-only live run on the rig (p50 140 ms). Showcase
   plan: `docs/SHOWCASE_PLAN.md`; poster: `reports/poster/`.
10. Next (`docs/LAB_DAY.md`): calibration with the monitor at 640x400, a moving commissioning scan that passes QA
   (the 5 Nov push failed: 81 s, 109 deg/s turns), landmark spans, latency trials, the 30-minute run.
11. Showcase and resume polish (21 Nov): landing page, case study, poster PDF and staging script merged as PR #10
   (`f94993e`), deployed (`docs/SHOWCASE.md` section 5), public repo synced (`aethertwin-lab`). Landing fixes and the
   link-preview rule are done; no faculty-guide line on the poster (owner). Replay data is served from GitHub Pages
   (repo `aethertwin-replay-data`, option D, `docs/SHOWCASE.md` section 4), live since 21 Nov 17:38 UTC: a view costs
   Vercel 0.4 MB. Every later showcase build needs `VITE_SHOWCASE_BASE` and `stage_showcase.py --data-dir --data-base`.
   A new export means a new commit to that data repo first. Open: deny rules for unused paths; usage checked by hand.
