import { describe, it, expect } from "vitest";
import { reportedSensorStatus, sensorWarnings } from "../src/lib/status";
import { scene } from "./fixtures";
const observed = {
  ...scene,
  source_mode: "sim",
  validity: {
    observations: true,
    localization: true,
    hardware_verified: false,
  },
  diagnostics: {
    observation_counts: { lidar: 2400, depth: 1200, legacy_s2: 0 },
  },
};
const fresh = { ageSeconds: 0.2, transport: "streaming" };
describe("reported sensor availability", () => {
  it("shows fresh positive actual counts without requiring an absent camera_available flag", () => {
    expect(reportedSensorStatus(observed, "lidar", fresh).tone).toBe("mint");
    const depth = reportedSensorStatus(observed, "depth", fresh);
    expect(depth.tone).toBe("mint");
    expect(depth.title).toContain("1200 reported points");
    expect(depth.title).toContain(
      "does not establish physical hardware verification",
    );
    expect(sensorWarnings(observed, fresh)).toEqual([]);
  });
  it("uses gray for unknown counts/freshness and never treats a scene or availability flag alone as a measurement", () => {
    expect(
      reportedSensorStatus(null, "lidar", {
        ageSeconds: null,
        transport: "offline",
      }).tone,
    ).toBe("muted");
    expect(reportedSensorStatus(scene, "lidar", fresh).tone).toBe("muted");
    expect(
      reportedSensorStatus(
        { ...scene, diagnostics: { camera_available: true } },
        "depth",
        fresh,
      ).tone,
    ).toBe("muted");
    expect(
      reportedSensorStatus(observed, "depth", { ageSeconds: null }).tone,
    ).toBe("muted");
  });
  it("marks explicit zero/invalid observations amber even if retained scene geometry exists", () => {
    const empty = {
      ...observed,
      validity: { observations: false, localization: false },
      diagnostics: { observation_counts: { lidar: 0, depth: 0, legacy_s2: 0 } },
    };
    expect(reportedSensorStatus(empty, "lidar", fresh).tone).toBe("amber");
    expect(reportedSensorStatus(empty, "depth", fresh).tone).toBe("amber");
    expect(sensorWarnings(empty, fresh).join(" ")).toContain(
      "No valid current observations",
    );
    expect(sensorWarnings(empty, fresh).join(" ")).toContain(
      "Localization invalid",
    );
    expect(
      reportedSensorStatus(
        { ...observed, validity: { observations: false } },
        "lidar",
        fresh,
      ).tone,
    ).toBe("amber");
    expect(
      reportedSensorStatus(
        { ...observed, diagnostics: { observation_counts: { lidar: "2400" } } },
        "lidar",
        fresh,
      ).tone,
    ).toBe("amber");
  });
  it("marks stale age or disconnected browser transport amber and preserves fallback freshness", () => {
    expect(
      reportedSensorStatus(observed, "lidar", {
        ageSeconds: 3.1,
        transport: "streaming",
      }).tone,
    ).toBe("amber");
    expect(
      reportedSensorStatus(observed, "depth", {
        ageSeconds: 0.1,
        transport: "offline",
      }).tone,
    ).toBe("amber");
    expect(
      sensorWarnings(observed, { ageSeconds: 8, transport: "streaming" }).join(
        " ",
      ),
    ).toContain("stale or disconnected");
    expect(
      reportedSensorStatus(observed, "depth", {
        ageSeconds: 0.1,
        transport: "fallback",
      }).tone,
    ).toBe("mint");
  });
  it("keeps modality availability and localization/navigation validity distinct", () => {
    const unlocalized = {
      ...observed,
      validity: { observations: true, localization: false },
    };
    expect(reportedSensorStatus(unlocalized, "lidar", fresh).tone).toBe("mint");
    expect(sensorWarnings(unlocalized, fresh)).toContain(
      "Localization invalid; motion and navigation must remain held.",
    );
    const lidarEmpty = {
      ...observed,
      diagnostics: { observation_counts: { lidar: 0, depth: 100 } },
    };
    expect(reportedSensorStatus(lidarEmpty, "lidar", fresh).tone).toBe("amber");
    expect(reportedSensorStatus(lidarEmpty, "depth", fresh).tone).toBe("mint");
    expect(sensorWarnings(lidarEmpty, fresh)).toContain(
      "3D LiDAR reports zero current points.",
    );
  });
  it("honors explicit missing camera and retains optional S2 failure information", () => {
    const missing = {
      ...observed,
      diagnostics: {
        ...observed.diagnostics,
        camera_available: false,
        camera_error: "profile missing",
        s2_degraded: true,
        s2_error: "serial disconnected",
      },
    };
    expect(reportedSensorStatus(missing, "depth", fresh).tone).toBe("amber");
    const warnings = sensorWarnings(missing, fresh).join(" ");
    expect(warnings).toContain("profile missing");
    expect(warnings).toContain("serial disconnected");
  });
});
