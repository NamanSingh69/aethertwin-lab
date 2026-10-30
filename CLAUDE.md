# AetherTwin — working notes for Claude

**Next phase (Windows desktop + physical rig): read `NEXT_AGENT.md` first.** It is the task list and
rulebook; `docs/HARDWARE_SETUP.md` is the bench guide for the human. `HANDOFF.md` is history from the
improvement pass. Keep `PROGRESS.md` local/untracked.

Python 3.12 research platform (LiDAR/RGB-D perception, mapping, tracking, planning) plus a
React 19 / TypeScript 7 / Vite 8 / Three.js r186 browser studio in `web/`. Honesty about
evidence is a core convention: UI and docs never imply hardware verification or accuracy
that was not measured; visual effects are presentation-only and must say so.

## Layout
- `command_center/` FastAPI service (`service.py`), auth, controls, binary scene protocol v2, gateway.
- `web/src/` studio: `App.tsx`, `components/`, `lib/` (api, status, types), `scene/` (Three.js
  `SpatialRenderer`, protocol decoder, worker). `web/dist/` is COMMITTED and served by Python.
- `web/tests/` Vitest unit/contract tests; `web/e2e/` Playwright (`mock/`, `live/`, `support/`).
- `tests/` pytest; `tools/` scripts; `docs/` architecture/status; `reports/`, `artifacts/` evidence.

## Commands (keep output lean: pipe through `tail`/`grep`)
- Python env: `uv venv -p 3.12 .venv && uv pip install --python .venv/bin/python --index-strategy unsafe-best-match --require-hashes -r requirements-ubuntu.lock`
- Python tests: `.venv/bin/python -m pytest -q -p no:cacheprovider 2>&1 | tail -5` (~100 s, 358 pass, 2 skip)
- Windows: `.venv\Scripts\python.exe` instead of `.venv/bin/python`; install with plain
  `pip install --require-hashes -r requirements-windows.lock`.
- Web (in `web/`): `npm ci`, `npm run typecheck`, `npm test`, `npm run build` (rebuilds `dist/`; must leave git clean if source unchanged)
- E2E mock (needs fresh `npm run build`): `npx playwright test --project=mock --reporter=line 2>&1 | tail -15`
- E2E live: start backend `setsid .venv/bin/python -m command_center --mode sim --threads 2 --output /tmp/at &`,
  then `AETHERTWIN_SERVICE_URL=http://127.0.0.1:8080 npx playwright test --project=live --reporter=line`
- Manifest (CI checks it): after staging, `python3 tools/repository_manifest.py && git add SOURCE_MANIFEST.json`
  (it hashes the working tree: stash unrelated edits first or the committed manifest is wrong)
- Bundle budget: `npm run size` after a build. GPU benchmark: `node tools/run-benchmark.mjs --mode streaming
  --quality balanced --out ../artifacts/renderer_benchmarks/x.json` (headed Chrome, real GPU).
- Cloud GPU (Modal): `modal run tools/modal_bench.py --suite smoke --gpu T4` (see reports/modal/README.md).

## Gotchas
- `vite preview` proxies `/api` and `/ws` to :8080. Mock E2E relies on the AUTO `service` fixture
  (`e2e/support/test.ts`); a non-auto fixture silently lets tests hit a real local backend.
- Mock E2E serves `web/dist`, so rebuild before running it after source edits.
- `@axe-core/playwright` is pinned to our `playwright-core` via npm `overrides`.
- `uv` needs `--index-strategy unsafe-best-match` for the lock (pip semantics across two indexes).
- Headless WebGL needs `--use-angle=swiftshader --enable-unsafe-swiftshader` (set in playwright.config.ts).
- `pkill -f <pattern>` kills your own shell when the pattern is in the command line; stop the service by PID.
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

## Remaining plan (update as tasks complete)
1. Playwright E2E (mock + live + axe) — done (`3222c84`).
2. Refactor `App.tsx` into hooks/components — done (`af0a6b2`).
3. Renderer perf, cinematic effects, camera animation — done (`a1e681d`, `fc92cdb`).
4. UX: shortcuts, help overlay, quality selector, toast, map progress — done (`00d8efe`).
5. Modal cloud GPU benchmarks + CUDA lock — done (`96c62a4`).
6. CI/CD — done (`c0d8fff`). IoT gateway + halo hardening — done (`29ed107`).
7. `docs/REVIEW_AND_ROADMAP.md` (backlog lives there), security review, PR #1 merged to main.
8. Next: hardware bring-up, the Pi-to-Windows raw-sensor transport and commissioning (`NEXT_AGENT.md`).
