# AetherTwin public showcase: hostile review, 21 Nov 2026

Read-only review by an independent reviewer (no tests run, nothing edited). Verdict: pass with fixes. The page is static,
headers are strict, no sourcemaps or secrets ship, every figure on the page matches its source file, and all text
colour pairs pass 4.5:1. Paste this into the AetherTwin session, or hand it the file.

## Must act
1. **High: 22 MB of replay data is served from Vercel** (`showcase/`, 65 files, in the deployed folder
   `aethertwin_demo/vercel_showcase_2026-11-21`; the bundle sets no `VITE_SHOWCASE_BASE`). One replay view is about
   75 requests and 23 MB. The Hobby team has 100 GB a month, about 4,300 replay views; at the documented limit of
   300 requests a minute per IP, one address can pull about 92 MB a minute and use the whole team's bandwidth in
   about 18 hours. Request limits do not cap bytes. The owner's Vercel gate asks for heavy assets off Vercel, and
   docs/SHOWCASE.md option A says the same; no decision between options A, B and C is recorded.
   Fix (needs the owner: R2 bucket and a CORS rule): move the replay data to Cloudflare R2, rebuild with
   `VITE_SHOWCASE_BASE`, check the deployed folder has no `showcase/` and the live replay plays.
2. **Medium: firewall state is not evidenced.** SHOWCASE.md lists rate limit and bot protection as a step to check
   and has no deny rules for unused paths and no check that Deployment Protection is on for previews.
   PROGRESS.md:137 "pass" covers the predeploy gate and a browser check only. Needs the owner in the Vercel
   dashboard; write what is found into SHOWCASE.md step 5.

## Small fixes an agent can do alone (one card)
- Tap targets: `.btn.small` is 40 px (landing.css:232, the top-bar "Launch replay" button); footer nav links are
  about 21 px tall (landing.css:937-944). Make both at least 44 px.
- Links point to `./replay.html` and `./explainer.html`; with `cleanUrls: true` each click costs a 308 redirect.
  Link to the clean URLs. Add a cache rule for `/og.jpg` and `/favicon.svg`.
- The page says "~510 tests" (landing.html:146, 330); today's CI counted 718 Python tests. Update from POSTER_FACTS.
- The public mirror's NEXT_AGENT.md still has a private-range LAN address (lines 116, 142, 158).

## To verify
- The showcase e2e test runs only with reduced motion on (showcase.spec.ts:170), so the animating path is untested.
- The 375 px overflow check reads `scrollWidth` while `body { overflow-x: hidden }` may hide clipped content.

## Next task cards for the project
1. HITL: replay data to R2 (above). 2. HITL: live firewall audit (above). 3. AFK: the small landing fixes.
4. HITL: camera-LiDAR calibration with the monitor at 640x400 (docs/LAB_DAY.md). 5. HITL: a moving scan that
passes QA (turns at or below 60 deg/s), then the 30-minute run, to unlock `hardware_verified`.

## Measured afterwards (21 Nov 2026, 20:03): rendered audit of `web/showcase-dist` served over http
`py ~/.claude/skills/ui-design/scripts/render_audit.py http://127.0.0.1:8765/index.html --out DIR` (build folder
served with `py -m http.server`; over file:// the CSS is blocked and the numbers are wrong). 390 and 1440 px:
457 text elements, 0 contrast failures, 0 overflow, 0 console errors. Tap targets at 390 px under 24 px high:
footer links "3D REPLAY" (two, 68x16), "GITHUB" (46x16), "TOP" (23x16) and `a.more` "Open it" (354x20).
Under 44 px: `a.wordmark` (146x26), `a.btn.small` "Launch replay" (126x40). Two in-sentence links are exempt.

## Status (21 Nov 2026, evening, after PR #10 was merged and deployed)
- Done: "~510 tests" is gone from the page (700+ / 713, PR #10, live since 13:59 UTC). The firewall is now evidenced:
  read through the API before that deploy (`docs/SHOWCASE.md` section 5).
- Still open, owner first:
  1. Replay data still on Vercel. Option A, R2 via `VITE_SHOWCASE_BASE`, needs the owner's Cloudflare bucket and a
     CORS rule.
  2. Done 21 Nov 2026 evening: link-preview bypass rule added on the owner's yes (`docs/SHOWCASE.md` section 5).
  3. There are no deny rules for unused paths.
  4. The owner checks team usage by hand on the dashboard (Hobby has no alerts).
- Done 21 Nov 2026 16:57 UTC (owner: "finish all the work"): tap targets, clean URLs and the cache rule, deployed
  (`docs/SHOWCASE.md` section 5). The two remaining small targets are links inside sentences, which WCAG exempts.
- Team usage read through the API on 21 Nov: 3,023 edge requests and 0.21 GB in 30 days (0.3 % and 0.2 % of the caps).
- Kept on purpose: the public repo keeps private-range LAN addresses. They are the Windows hotspot default
  (192.168.137.x) and the Unitree L2 factory default (192.168.1.x), and the owner chose to keep them (21 Nov). Pi
  login, hostname, serials, MACs and user folders were replaced.
- Verified: deployment URLs other than production need a Vercel login (302 to vercel.com/sso-api).
