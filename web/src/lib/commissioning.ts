export type JobKind =
  | "discovery"
  | "diagnostics"
  | "calibration_candidate"
  | "build_twin"
  | "coverage"
  | "relocalization";
export interface RegisteredSource {
  id: string;
  label: string;
  mode: string;
  static_scene: boolean;
  fixed_position: boolean;
}
export interface CommissioningJob {
  id: string;
  kind: JobKind;
  label: string;
  status: string;
  reason?: string | null;
  source_id?: string;
  twin_id?: string;
  correspondence_id?: string;
  max_frames: number;
  created_at_unix: number;
  promotion_performed: boolean;
  logs: Array<{ message: string; at_unix: number }>;
  artifacts: Array<{ name: string; bytes: number; url: string }>;
  summary?: Record<string, unknown> | null;
}
export interface CommissioningInventory {
  enabled: boolean;
  sources: RegisteredSource[];
  twins: Array<{ id: string; label: string }>;
  correspondence_files: Array<{
    id: string;
    label: string;
    measured_declared: boolean;
    independent_validation_declared: boolean;
  }>;
  kinds: Array<{ kind: JobKind; label: string }>;
  limits: {
    queue_capacity: number;
    max_frames: number;
    max_job_seconds: number;
    aggregate_output_bytes: number;
    measured_output_bytes: number;
    concurrent_jobs: number;
  };
  live_jobs_enabled: boolean;
  live_job_reason: string;
  automatic_promotion: boolean;
  jobs: CommissioningJob[];
}
export const commissioningSteps: Array<{
  kind: JobKind;
  title: string;
  description: string;
}> = [
  {
    kind: "discovery",
    title: "Discover resources",
    description:
      "Read configured inputs and SDK availability. No device is opened.",
  },
  {
    kind: "diagnostics",
    title: "Run diagnostics",
    description:
      "Exercise bounded observations and inspect software and hardware checks separately.",
  },
  {
    kind: "calibration_candidate",
    title: "Export calibration",
    description:
      "Fit registered 3D/2D targets and retain provenance, uncertainty and held-out evidence.",
  },
  {
    kind: "build_twin",
    title: "Scan a candidate twin",
    description:
      "Reconstruct designated static observations into a separate candidate map.",
  },
  {
    kind: "coverage",
    title: "Review coverage",
    description:
      "Count measured evidence in a reference map. Unobserved space stays unknown.",
  },
  {
    kind: "relocalization",
    title: "Review fixed position",
    description:
      "Measure registration repeatability from an explicitly fixed-position source.",
  },
];
export function commissioningBlock(
  inventory: CommissioningInventory | null,
  role: string | undefined,
  kind: JobKind,
  sourceId: string,
  twinId: string,
  correspondenceId: string,
  frames: number,
) {
  if (!inventory?.enabled)
    return "Commissioning is not available on this service.";
  if (role !== "operator")
    return "An operator session is required to start or cancel jobs.";
  if (!inventory.kinds.some((item) => item.kind === kind))
    return "This job is not supported by the service.";
  if (
    !Number.isInteger(frames) ||
    frames < 1 ||
    frames > inventory.limits.max_frames
  )
    return `Choose 1–${inventory.limits.max_frames} frames.`;
  if (
    inventory.limits.measured_output_bytes >=
    inventory.limits.aggregate_output_bytes
  )
    return "The retained artifact quota is full. Export and archive evidence before more work.";
  if (
    inventory.jobs.filter((job) => job.status === "queued").length >=
    inventory.limits.queue_capacity
  )
    return "The bounded job queue is full.";
  if (["diagnostics", "build_twin", "relocalization"].includes(kind)) {
    const source = inventory.sources.find((item) => item.id === sourceId);
    if (!source) return "Choose a registered observation source.";
    if (source.mode === "live") return inventory.live_job_reason;
    if (kind === "build_twin" && !source.static_scene)
      return "Choose a source explicitly registered as static.";
    if (kind === "relocalization" && !source.fixed_position)
      return "Choose a source explicitly registered as fixed-position.";
  }
  if (
    ["diagnostics", "coverage", "relocalization"].includes(kind) &&
    !inventory.twins.some((item) => item.id === twinId)
  )
    return "Choose a registered reference twin.";
  if (
    kind === "calibration_candidate" &&
    !inventory.correspondence_files.some((item) => item.id === correspondenceId)
  )
    return "No registered correspondence file is selected.";
  return null;
}
export function commissioningPayload(
  kind: JobKind,
  sourceId: string,
  twinId: string,
  correspondenceId: string,
  frames: number,
  requestId: string,
) {
  return {
    kind,
    request_id: requestId,
    max_frames: frames,
    ...(["diagnostics", "build_twin", "relocalization"].includes(kind)
      ? { source_id: sourceId }
      : {}),
    ...(["diagnostics", "coverage", "relocalization"].includes(kind)
      ? { twin_id: twinId }
      : {}),
    ...(kind === "calibration_candidate"
      ? { correspondence_id: correspondenceId }
      : {}),
  };
}
export function commissioningChecks(
  summary: Record<string, unknown> | null | undefined,
): Array<{ label: string; value: string }> {
  if (!summary) return [];
  const rows: Array<{ label: string; value: string }> = [];
  for (const key of [
    "software_checks_passed",
    "hardware_checks_passed",
    "hardware_status",
    "review_status",
    "source",
    "actual_frames",
    "occupied_cells",
    "free_cells",
    "known_fraction",
    "coverage_fraction",
    "known_cells",
    "measured",
  ]) {
    const value = summary[key];
    if (["string", "number", "boolean"].includes(typeof value))
      rows.push({ label: key.replaceAll("_", " "), value: String(value) });
  }
  if (
    summary.validation &&
    typeof summary.validation === "object" &&
    "status" in summary.validation
  )
    rows.push({
      label: "independent validation",
      value: String(summary.validation.status),
    });
  for (const [name, stage] of Object.entries(
    summary.stages && typeof summary.stages === "object" ? summary.stages : {},
  )) {
    if (stage && typeof stage === "object" && "status" in stage)
      rows.push({
        label: name.replaceAll("_", " "),
        value: String(stage.status),
      });
  }
  return rows.slice(0, 24);
}
