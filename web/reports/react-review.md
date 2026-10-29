# Frontend review — 2026-10-28

Applied the installed `vercel:react-best-practices` checklist after the final
multi-component changes. This is a source/component review, not a substitute
for the separate interactive browser acceptance or a 1080p performance run.

- Hooks remain unconditional. Effects clean up their worker, socket, timer,
  observer, animation frame and listeners. Commissioning polls sequentially,
  aborts on unmount and does not start a second polling interval per response.
- Scene typed arrays and transient renderer state stay outside React state.
  Metadata is bounded; event history, jobs, downloads, descriptor rows and track
  lists have explicit limits. Heavy Three.js code is dynamically imported.
- Periodic bootstrap requests exclude the large latest JSON scene while the
  binary stream runs. Only the initial bootstrap includes its fallback frame.
- Benchmark and studio entries have separate dependency graphs. The production
  startup check verifies benchmark startup without mounting the studio or loading
  its layout CSS. The benchmark fixture is explicitly synthetic/static.
- Inputs have visible labels; icon buttons have accessible names. Native buttons,
  selects, details, forms and download links retain keyboard semantics. Disabled
  actions have nearby reasons. Six commissioning stages have pressed state.
- The geofence modal traps focus, supports Escape and restores focus. Commissioning
  is an ordinary scrollable workspace, not a modal, so it does not trap focus.
- Responsive controls retain all four workspaces, source selection and entity
  selection. Commissioning scrolls within the small viewport and collapses to one
  column below 900 pixels; it does not remove actions at mobile widths.
- Reduced-motion CSS disables transitions/animations. Three.js orbit damping is
  disabled when `prefers-reduced-motion: reduce` is active. No commissioning
  animation or automatic camera movement was added.
- Predicted tracks have dashed, dimmer boxes and explicit measurement age. Global
  source-age warnings remain visible. Missing tracking provenance stays unreported.
- Job completion is separate from passed/failed/not-tested checks. Viewer sessions
  cannot start/cancel jobs; real server role and registry checks remain authoritative.
  Calibration/maps/poses are never promoted by the panel.

Validation: 40 frontend tests passed, one optional live-service integration test
skipped without its explicit service URL. TypeScript, production build and the
production benchmark startup gate passed. Full browser interaction is checked
separately by the parent task using the permitted browser surface.

Final sensor-status correction: dock lights use reported LiDAR/depth counts,
observation validity, source age and browser transport. Positive fresh counts
are green, explicit zero/invalid or stale data amber, unreported evidence gray.
Tooltips expose declared counts and separate availability from physical
verification. Empty-observation and localization warnings retain map/track context.
