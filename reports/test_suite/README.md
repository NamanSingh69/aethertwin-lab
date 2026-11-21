# Test suite counts

Counted on 21 Nov 2026 on a Linux cloud container (4 cores, Python 3.12, run as root), branch
`claude/festive-ritchie-h541dj` at commit `659fbef`. These are counts of automated software tests; none is a hardware result.

| Suite | Command | Result |
|---|---|---|
| Python (pytest) | `python tools/fast_check.py --full` | 713 passed, 11 skipped (89 files, 221 s) |
| Web unit (Vitest) | `npm test` in `web/` | 88 passed, 1 skipped |
| Browser, mock service (Playwright + axe) | `npx playwright test --project=mock` | 28 passed |
| Browser, static showcase (Playwright + axe) | `npx playwright test --project=showcase` | 12 passed |

Later the same day, after `tools/stage_showcase.py` and its 6 tests were added: pytest 719 passed, 11 skipped
(90 files, 193 s), and the live-service browser project (`--project=live`, against
`command_center --mode sim`) 4 passed. The landing page keeps the dated figure of 713.

Notes:
- One of the 11 skips is `tests/test_capture_hotspot.py::test_bootstrap_is_bash_clean_and_renders_the_unit_for_the_login_user`,
  skipped because the container runs as root and `pi_bootstrap.sh` refuses root. The others need Windows, a
  compiler or hardware.
- The Windows count was last recorded on 5 Nov 2026 as 718 passed (`docs/aide2_tree.jsonl`, cycle C46, private
  check 718/718); not re-run in the container.

## Windows (operator laptop), 21 Nov 2026

Windows 11, Python 3.12.10, same branch at commit `59c4797`, not run as root:

| Suite | Command | Result |
|---|---|---|
| Python (pytest) | `python tools/fast_check.py --full` | 725 passed, 6 skipped, 0 failed (90 files, 8 workers, 294 s) |
| Web unit (Vitest) | `npm test` in `web/` | 88 passed, 1 skipped |

The Windows count is higher than the container's 719 because fewer tests are skipped here (6 instead of 11). The
landing page keeps the dated container figure of 713.
