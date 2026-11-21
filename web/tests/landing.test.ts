// The showcase landing page (web/landing.html) is static HTML. Every figure on
// it names the repository file it comes from (data-source) and a phrase that
// must appear in that file (data-evidence), so the page cannot drift from the
// recorded evidence. The hero's point file must match its metadata.
import { existsSync, readFileSync, statSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import meta from "../src/landing/twin-points.json";

const web = resolve(import.meta.dirname, ".."),
  repo = resolve(web, ".."),
  html = readFileSync(resolve(web, "landing.html"), "utf8");
const squash = (text: string) => text.replace(/\*\*/g, "").replace(/\s+/g, " ");
const facts = [...html.matchAll(/data-source="([^"]+)"\s+data-evidence="([^"]+)"/g)].map(([, source, evidence]) => ({
  source: source!,
  evidence: evidence!,
}));

describe("showcase landing page", () => {
  it("backs every figure with a phrase from a file in the repository", () => {
    expect(facts.length).toBeGreaterThanOrEqual(10);
    for (const { source, evidence } of facts) {
      expect(existsSync(resolve(repo, source)), source).toBe(true);
      expect(squash(readFileSync(resolve(repo, source), "utf8")), `${source}: ${evidence}`).toContain(evidence);
    }
  });

  it("leaves no ledger entry or measured bar without a source", () => {
    const tiles = html.match(/<div class="entry"[^>]*>/g) ?? [],
      bars = html.match(/<li (?:class="live" )?style="--v: \d+"[^>]*>/g) ?? [];
    expect(tiles.length).toBe(6);
    expect(bars.length).toBe(5);
    for (const tag of [...tiles, ...bars]) expect(tag).toMatch(/data-source="[^"]+"\s+data-evidence="[^"]+"/);
  });

  it("says the replay is recorded, states the limits, and makes no verification claim", () => {
    const text = html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
    for (const phrase of [
      "Recorded, not live",
      "A fit, not accuracy",
      "older ones are skipped by design",
      "not live. Each change",
      "trained on synthetic data only",
      "No camera-LiDAR calibration yet",
      "No accuracy against ground truth",
      "pushed by hand",
      "planned work, not a result",
      "never connected to the rig",
    ])
      expect(text, phrase).toContain(phrase);
    for (const phrase of ["millimet", "guarantee", "certified", "state-of-the-art", "100%"])
      expect(text.toLowerCase(), phrase).not.toContain(phrase);
    // "verified" appears only to say what is NOT verified yet or what a later phase must show.
    for (const hit of text.matchAll(/.{0,60}verified.{0,20}/gi))
      expect(hit[0]).toMatch(/not marked hardware-verified|30-minute verified run/);
  });

  it("links only to pages of the static site and the public repository", () => {
    const hrefs = [...html.matchAll(/href="([^"]+)"/g)].map((m) => m[1]!);
    for (const href of hrefs)
      expect(href).toMatch(/^(#[\w-]+|\.\/|\.\/replay|\.\/explainer|https:\/\/github\.com\/NamanSingh69\/aethertwin-lab)$/);
    expect(html).not.toMatch(/\/api\/|\/ws\/|wss?:\/\//);
  });

  it("ships hero points that match their metadata", () => {
    const bytes = statSync(resolve(web, "src/landing/twin-points.bin")).size;
    expect(bytes).toBe(meta.count * 6);
    expect(meta.count).toBeLessThanOrEqual(meta.total_measured_points);
    const raw = readFileSync(resolve(web, "src/landing/twin-points.bin")),
      points = new Int16Array(raw.buffer, raw.byteOffset, meta.count * 3);
    for (let axis = 0; axis < 3; axis++) {
      let low = Infinity,
        high = -Infinity;
      for (let i = axis; i < points.length; i += 3) {
        low = Math.min(low, points[i]!);
        high = Math.max(high, points[i]!);
      }
      expect([low, high]).toEqual([meta.min_cm[axis], meta.max_cm[axis]]);
    }
    // The plate's static readouts (shown before the script runs) agree with the metadata.
    const metres = (cm: number) => (cm / 100).toFixed(1),
      extent = [0, 1, 2].map((axis) => metres(meta.max_cm[axis]! - meta.min_cm[axis]!)).join(" × ");
    expect(html).toContain(`<dd id="read-points">${meta.count.toLocaleString("en")} of ${meta.total_measured_points.toLocaleString("en")}</dd>`);
    expect(html).toContain(`<dd id="read-voxel">${Math.round(meta.voxel_size_m * 100)} cm</dd>`);
    expect(html).toContain(`<dd id="read-extent">${extent} m</dd>`);
    expect(html).toContain(`<span id="read-top">${metres(meta.max_cm[2]!)} m</span>`);
    // The caption quotes the twin's point count; reports/lab/POSTER_FACTS.md records the same number.
    expect(readFileSync(resolve(repo, "reports/lab/POSTER_FACTS.md"), "utf8")).toContain(
      `${meta.total_measured_points.toLocaleString("en")} surface points`,
    );
  });
});
