import { describe, it, expect, vi, afterEach } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import {
  centerZoneClearance,
  parsePoint,
  positionStd,
  validZone,
} from "../src/lib/geometry";
import {
  canControl,
  sourceLabel,
  sensorWarnings,
  observationIdentity,
  metadataOnly,
} from "../src/lib/status";
import { sendCommand } from "../src/lib/api";
import { Inspector } from "../src/components/Inspector";
import App from "../src/App";
import { scene, track } from "./fixtures";
import type { Bootstrap, Zone } from "../src/lib/types";
const bootstrap: Bootstrap = {
  protocol_version: 2,
  identity: "test",
  role: "operator",
  source_mode: "sim",
  hardware_status: "not_tested",
  map_revision: "map-1",
  map_manifest_url: "/map",
  control_revision: 4,
  capabilities: { pause: true, seek: false },
  latest: null,
};
describe("truthful controls and geometry", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("requires explicit operator capability and honors unsupported controls", () => {
    expect(canControl(bootstrap, "pause")).toBe(true);
    expect(canControl(bootstrap, "seek")).toBe(false);
    expect(canControl({ ...bootstrap, role: "viewer" }, "pause")).toBe(false);
    expect(canControl(bootstrap, "set_start_goal")).toBe(false);
  });
  it("uses the current replay source instead of recorded synthetic origin", () => {
    expect(
      sourceLabel(
        { ...scene, diagnostics: { source_mode: "replay", synthetic: true } },
        bootstrap,
      ),
    ).toBe("Replay");
    expect(sensorWarnings(scene)).toEqual([]);
    expect(
      sensorWarnings({
        ...scene,
        diagnostics: { s2_degraded: true, painting: "camera_unavailable" },
      }),
    ).toHaveLength(2);
  });
  it("keeps repeated snapshot identity stable rather than inventing fresh observations", () => {
    expect(observationIdentity(scene, "stream")).toBe(
      observationIdentity({ ...scene }, "stream"),
    );
    expect(observationIdentity({ ...scene, frame_id: 11 }, "stream")).not.toBe(
      observationIdentity(scene, "stream"),
    );
  });
  it("keeps fallback bulk arrays out of React metadata without mutating input", () => {
    const metadata = metadataOnly(scene);
    expect(metadata.voxels?.centers).toBeUndefined();
    expect(metadata.path).toBeUndefined();
    expect(metadata.tracks).toBe(scene.tracks);
    expect(scene.voxels?.centers).toHaveLength(1);
  });
  it("computes signed CENTER distance for rotated, intersecting and outside zones", () => {
    const zone: Zone = {
      name: "Rotated",
      center: [0, 0, 0],
      half_extents: [2, 1, 1],
      yaw: Math.PI / 2,
      severity_level: 2,
    };
    expect(centerZoneClearance([0, 0, 0], zone)).toBe(-1);
    expect(centerZoneClearance([0, 3, 0], zone)).toBeCloseTo(1);
    expect(centerZoneClearance([2, 0, 0], zone)).toBeCloseTo(1);
    expect(centerZoneClearance([0, 2, 0], zone)).toBeCloseTo(0);
  });
  it("validates finite route and zone edits", () => {
    expect(parsePoint("1, 2, .6")).toEqual([1, 2, 0.6]);
    for (const input of ["1,2", "1,,2", "NaN,2,3", "Infinity,0,0"])
      expect(() => parsePoint(input)).toThrow();
    expect(
      validZone({
        name: "z",
        center: [0, 0, 0],
        half_extents: [1, 1, 1],
        yaw: 0,
        severity_level: 2,
      }),
    ).toBe(true);
    expect(
      validZone({
        name: "z",
        center: [0, 0, 0],
        half_extents: [-1, 1, 1],
        yaw: 0,
        severity_level: 2,
      }),
    ).toBe(false);
  });
  it("reports measured position uncertainty and never creates missing covariance", () => {
    expect(positionStd(track)).toEqual([0.2, 0.3, 0.4]);
    expect(positionStd({ ...track, covariance: undefined })).toBeNull();
  });
  it("sends transactional revisions, map identity and session cookies", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ status: "accepted", revision: 5 }),
    });
    vi.stubGlobal("fetch", fetchMock);
    await sendCommand(bootstrap, "pause", {}, 2);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v2/commands");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body)).toMatchObject({
      expected_revision: 2,
      map_revision: "map-1",
      action: "pause",
    });
  });
  it("renders actual entity values with precise clearance and uncertainty labels", () => {
    const html = renderToStaticMarkup(
      createElement(Inspector, {
        scene,
        selected: 7,
        onSelect: () => {},
        onFocus: () => {},
        onClear: () => {},
        latencies: [250],
      }),
    );
    expect(html).toContain("Robot cart");
    expect(html).toContain("Position σ, XYZ");
    expect(html).toContain("0.200, 0.300, 0.400 m");
    expect(html).toContain("No predicted contact");
    expect(html).not.toContain("60 FPS");
  });
  it("retains all workspaces and keyboard track/source choices in the compact dock", () => {
    const html = renderToStaticMarkup(createElement(App));
    const menu =
      html.match(/aria-label="All workspaces"([\s\S]*?)<\/nav>/)?.[1] ?? "";
    for (const label of ["Explore", "Live", "Replay", "Commission"])
      expect(menu).toContain(label);
    expect(html).toContain('aria-label="Mobile data source"');
    expect(html).toContain('aria-label="Select tracked entity"');
  });
});
