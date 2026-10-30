import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { CHAPTERS, MEASUREMENTS, ONE_LINER, PITCH, PROOF, ROADMAP } from "../src/explainer/content";
import {
  LOG_ODDS,
  applyBeam,
  descriptors,
  eigenSymmetric3,
  entityRisk,
  sweep,
  timeToCollision,
  traverse,
} from "../src/explainer/math";
import { parseViewParams, studioLink } from "../src/lib/viewParams";
import { shapePoints } from "../src/explainer/Labs";
import { initialLayers } from "../src/lib/types";

const repo = resolve(import.meta.dirname, "../..");

describe("explainer content", () => {
  it("cites a source in the repository for every measurement", () => {
    for (const m of MEASUREMENTS) expect(existsSync(resolve(repo, m.source)), m.source).toBe(true);
  });

  it("points every chapter at code that exists", () => {
    for (const chapter of CHAPTERS)
      for (const path of chapter.code) expect(existsSync(resolve(repo, path)), path).toBe(true);
  });

  it("makes no hardware or accuracy claims", () => {
    const text = JSON.stringify([ONE_LINER, PITCH, CHAPTERS, MEASUREMENTS, PROOF, ROADMAP]).toLowerCase();
    for (const phrase of ["verified", "millimet", "guarantee", "zero-copy", "without dropping", "certified", "100% "])
      expect(text, phrase).not.toContain(phrase);
    expect(PROOF.some((p) => p.status === "not_yet" && /real sensors/i.test(p.item))).toBe(true);
  });

  it("uses only studio layers that exist", () => {
    for (const chapter of CHAPTERS)
      for (const layer of chapter.studio?.layers ?? []) expect(Object.keys(initialLayers)).toContain(layer);
  });
});

describe("explainer math ports", () => {
  it("matches the Python time-to-collision cases", () => {
    expect(timeToCollision([5, 0], [-1, 0], 0.9)).toBeCloseTo(4.1, 9);
    expect(timeToCollision([5, 0], [1, 0], 0.9)).toBe(Infinity);
    expect(timeToCollision([0.5, 0], [0, 0], 0.9)).toBe(0);
    expect(timeToCollision([5, 2], [-1, 0], 0.9)).toBe(Infinity);
  });

  it("stretches risk ahead of a moving entity", () => {
    expect(entityRisk([1, 0], [0, 0], [1.5, 0])).toBeGreaterThan(entityRisk([-1, 0], [0, 0], [1.5, 0]));
    expect(entityRisk([0, 0], [0, 0], [0, 0])).toBeCloseTo(1, 9);
  });

  it("finds eigenvalues of a rotated diagonal matrix", () => {
    const c = Math.cos(0.7), s = Math.sin(0.7);
    const r = [[c, -s, 0], [s, c, 0], [0, 0, 1]], d = [5, 2, 0.5];
    const m = [0, 1, 2].map((i) => [0, 1, 2].map((j) => r[i][0] * d[0] * r[j][0] + r[i][1] * d[1] * r[j][1] + r[i][2] * d[2] * r[j][2]));
    const { values, normal } = eigenSymmetric3(m);
    expect(values[0]).toBeCloseTo(5, 9);
    expect(values[1]).toBeCloseTo(2, 9);
    expect(values[2]).toBeCloseTo(0.5, 9);
    expect(Math.abs(normal[2])).toBeCloseTo(1, 9);
  });

  it("scores a flat patch as planar and a line as linear", () => {
    const plane = Array.from({ length: 49 }, (_, i) => [i % 7, Math.floor(i / 7), 0]);
    const line = Array.from({ length: 20 }, (_, i) => [i, 0.01 * (i % 2), 0]);
    expect(descriptors(plane).planarity).toBeGreaterThan(0.9);
    expect(descriptors(line).linearity).toBeGreaterThan(0.99);
  });

  it("gives each lab shape the descriptor it illustrates", () => {
    expect(descriptors(shapePoints("wall", 0.01)).planarity).toBeGreaterThan(0.8);
    expect(descriptors(shapePoints("pole", 0.01)).linearity).toBeGreaterThan(0.8);
    expect(descriptors(shapePoints("blob", 0.01)).scattering).toBeGreaterThan(0.5);
  });

  it("traverses every crossed cell and scores a beam", () => {
    expect(traverse([0.5, 0.5], [3.5, 0.5])).toEqual([[0, 0], [1, 0], [2, 0]]);
    const grid = new Float32Array(25);
    applyBeam(grid, 5, [0.5, 0.5], [3.5, 0.5]);
    const expected = [LOG_ODDS.free, LOG_ODDS.free, LOG_ODDS.free, LOG_ODDS.hit, 0];
    grid.slice(0, 5).forEach((value, i) => expect(value).toBeCloseTo(expected[i], 6));
  });

  it("puts deskewed points back on the room walls", () => {
    const onWall = (p: number[]) => Math.abs(Math.max(Math.abs(p[0]), Math.abs(p[1])) - 3) < 1e-6;
    expect(sweep(3, 5, true).every(onWall)).toBe(true);
    expect(sweep(3, 5, false).filter(onWall).length).toBeLessThan(40);
  });
});

describe("studio view links", () => {
  it("round-trips layers and camera view", () => {
    const link = studioLink(["path", "risk"], "top");
    const parsed = parseViewParams(link.slice(2));
    expect(parsed.view).toBe("top");
    expect(Object.entries(parsed.layers).filter(([, on]) => on).map(([name]) => name).sort()).toEqual(["path", "risk"]);
  });

  it("keeps defaults for missing or unknown values", () => {
    expect(parseViewParams("")).toEqual({ layers: initialLayers, view: null });
    expect(parseViewParams("?layers=bogus&view=sideways")).toEqual({ layers: initialLayers, view: null });
  });
});
