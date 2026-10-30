// Photo-capture appearance manifest (command_center/appearance.py, schema 1).
// The capture is visual only: it never feeds mapping, planning or risk.

export interface Appearance {
  file: string;
  /** Row-major 4x4 capture-to-world transform. */
  transform: number[];
  alignment: { method: string; rmse_m: number | null; checked_points: number | null };
  provenance: Record<string, string>;
}

export function parseAppearance(value: unknown): Appearance {
  const m = value as Partial<Appearance> & { schema_version?: number; kind?: string; visual_only?: boolean };
  if (!m || m.schema_version !== 1) throw new Error("Unsupported photo-capture manifest version");
  if (m.kind !== "gltf") throw new Error(`Unsupported photo-capture kind: ${String(m.kind)}`);
  if (m.visual_only !== true) throw new Error("Photo captures must be marked visual only");
  if (typeof m.file !== "string" || !/^[^/\\]+\.glb$/.test(m.file)) throw new Error("Photo-capture file must be a .glb name");
  if (!Array.isArray(m.transform) || m.transform.length !== 16 || !m.transform.every(Number.isFinite))
    throw new Error("Photo-capture transform must be 16 finite numbers");
  const alignment = m.alignment ?? { method: "none", rmse_m: null, checked_points: null };
  return { file: m.file, transform: m.transform, alignment, provenance: m.provenance ?? {} };
}

/** One line for the layer list: how far the capture can be trusted to line up. */
export function alignmentSummary({ alignment }: Appearance) {
  if (alignment.method === "none") return "Not aligned to the measured map";
  if (alignment.method === "manual") return "Aligned by eye, not measured";
  const rmse = alignment.rmse_m === null ? "unknown" : `${(alignment.rmse_m * 100).toFixed(1)} cm`;
  return `Aligned, error ${rmse}${alignment.checked_points ? ` over ${alignment.checked_points} points` : ""}`;
}
