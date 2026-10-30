import * as T from "three";

/**
 * Reusable overlay geometry: attributes are preallocated, written in place each
 * frame and uploaded only for the used range. Capacity grows by doubling and
 * never shrinks, so steady-state updates allocate nothing.
 */
export class GrowableGeometry {
  readonly geometry = new T.BufferGeometry();
  private capacity = 0;
  count = 0;
  constructor(
    private sizes: Record<string, number>,
    initialCapacity: number,
  ) {
    this.reserve(initialCapacity);
  }
  reserve(count: number) {
    if (count <= this.capacity) return;
    // Replacing attributes needs the old GPU buffers released first.
    if (this.capacity) this.geometry.dispose();
    this.capacity = Math.max(count, this.capacity * 2, 16);
    for (const [name, size] of Object.entries(this.sizes))
      this.geometry.setAttribute(
        name,
        new T.BufferAttribute(
          new Float32Array(this.capacity * size),
          size,
        ).setUsage(T.DynamicDrawUsage),
      );
  }
  array(name: string) {
    return (this.geometry.getAttribute(name) as T.BufferAttribute)
      .array as Float32Array;
  }
  /** Upload the first `count` vertices and draw only those. */
  commit(count: number) {
    this.count = count;
    for (const [name, size] of Object.entries(this.sizes)) {
      const attribute = this.geometry.getAttribute(name) as T.BufferAttribute;
      attribute.clearUpdateRanges();
      if (count) attribute.addUpdateRange(0, count * size);
      attribute.needsUpdate = count > 0;
    }
    this.geometry.setDrawRange(0, count);
  }
  /**
   * Dash distances for `LineDashedMaterial`, matching three's
   * `computeLineDistances` for `Line` (cumulative) or `LineSegments` (per pair).
   */
  lineDistances(count: number, segments: boolean) {
    const p = this.array("position"),
      d = this.array("lineDistance");
    for (let i = 0; i < count; i++) {
      if (i === 0 || (segments && i % 2 === 0)) {
        d[i] = segments && i ? d[i - 1] : 0;
        continue;
      }
      const a = (i - 1) * 3,
        b = i * 3;
      d[i] =
        d[i - 1] + Math.hypot(p[b] - p[a], p[b + 1] - p[a + 1], p[b + 2] - p[a + 2]);
    }
  }
  dispose() {
    this.geometry.dispose();
  }
}
