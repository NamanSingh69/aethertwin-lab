import { it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  HaloMirror,
  haloPixelPosition,
  validHaloPixels,
} from "../src/components/HaloMirror";
import type { HaloSnapshot } from "../src/lib/types";
const halo: HaloSnapshot = {
  pixel_count: 24,
  pixels: Array.from({ length: 24 }, (_, i) =>
    i === 0 ? [255, 0, 0] : [0, 0, 0],
  ),
  source: "sim",
  state_name: "critical",
  stale: false,
  watchdog: false,
  disconnected: false,
  source_age_ms: 125,
  nearest_track_id: 7,
  distance_m: 1.5,
  azimuth_degrees: 0,
  brightness: 255,
  risk_scale: 200,
  virtual: true,
  physical_verified: false,
  reason: "Synthetic protected-zone breach",
};
it("preserves the actual 24 uint8 LED values and explicit unverified physical status", () => {
  expect(validHaloPixels(halo)).toBe(true);
  const html = renderToStaticMarkup(createElement(HaloMirror, { halo }));
  expect(html).toContain("rgb(255,0,0)");
  expect(html.match(/data-index=/g)).toHaveLength(24);
  expect(html).toContain("Physical output not verified");
  expect(html).toContain("Source age 125 ms");
});
it("maps rig-forward index 0 up and rig-left index 6 left, without world-frame assumptions", () => {
  expect(haloPixelPosition(0)).toEqual([48, 17]);
  expect(haloPixelPosition(6)).toEqual([17, 48]);
  expect(haloPixelPosition(12)).toEqual([48, 79]);
  expect(haloPixelPosition(18)).toEqual([79, 48]);
});
it("does not invent pixels for missing or malformed mirror data", () => {
  expect(validHaloPixels(null)).toBe(false);
  expect(validHaloPixels({ ...halo, pixels: [[256, 0, 0]] })).toBe(false);
  const html = renderToStaticMarkup(createElement(HaloMirror, { halo: null }));
  expect(html).toContain("not reported");
  expect(html).not.toContain("rgb(");
});
