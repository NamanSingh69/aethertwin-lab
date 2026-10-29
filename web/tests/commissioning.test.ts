import { it, expect } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  CommissioningPanel,
  CommissioningResults,
} from "../src/components/CommissioningPanel";
import {
  commissioningBlock,
  commissioningChecks,
  commissioningPayload,
  type CommissioningInventory,
  type CommissioningJob,
} from "../src/lib/commissioning";
const inventory: CommissioningInventory = {
  enabled: true,
  sources: [
    {
      id: "sim",
      label: "Dedicated synthetic scan",
      mode: "sim",
      static_scene: true,
      fixed_position: false,
    },
    {
      id: "moving",
      label: "Moving recording",
      mode: "replay",
      static_scene: false,
      fixed_position: false,
    },
    {
      id: "live",
      label: "Rig",
      mode: "live",
      static_scene: true,
      fixed_position: true,
    },
  ],
  twins: [{ id: "active", label: "Measured reference" }],
  correspondence_files: [
    {
      id: "board",
      label: "Board targets",
      measured_declared: true,
      independent_validation_declared: false,
    },
  ],
  kinds: [
    "discovery",
    "diagnostics",
    "calibration_candidate",
    "build_twin",
    "coverage",
    "relocalization",
  ].map((kind) => ({ kind: kind as any, label: kind })),
  limits: {
    queue_capacity: 2,
    max_frames: 128,
    max_job_seconds: 300,
    aggregate_output_bytes: 1000,
    measured_output_bytes: 0,
    concurrent_jobs: 1,
  },
  live_jobs_enabled: false,
  live_job_reason: "Exclusive rig required",
  automatic_promotion: false,
  jobs: [],
};
it("gates job starts against explicit role, source declarations, live ownership and bounded budget", () => {
  const blocked = (
    kind: Parameters<typeof commissioningBlock>[2],
    source = "sim",
    role = "operator",
    frames = 8,
  ) =>
    commissioningBlock(
      inventory,
      role,
      kind,
      source,
      "active",
      "board",
      frames,
    );
  expect(blocked("discovery")).toBeNull();
  expect(blocked("discovery", "sim", "viewer")).toContain("operator");
  expect(blocked("build_twin", "moving")).toContain("static");
  expect(blocked("relocalization")).toContain("fixed-position");
  expect(blocked("diagnostics", "live")).toContain("Exclusive rig");
  expect(blocked("diagnostics", "sim", "operator", 129)).toContain("128");
  expect(
    commissioningBlock(
      {
        ...inventory,
        limits: { ...inventory.limits, measured_output_bytes: 1000 },
      },
      "operator",
      "discovery",
      "",
      "",
      "",
      8,
    ),
  ).toContain("quota");
  expect(
    commissioningBlock(
      inventory,
      "operator",
      "calibration_candidate",
      "",
      "",
      "unknown",
      8,
    ),
  ).toContain("registered");
});
it("sends only selected registered IDs, bounded count and idempotency key", () => {
  expect(
    commissioningPayload(
      "build_twin",
      "sim",
      "unused",
      "unused",
      12,
      "request-1",
    ),
  ).toEqual({
    kind: "build_twin",
    source_id: "sim",
    max_frames: 12,
    request_id: "request-1",
  });
  expect(
    commissioningPayload(
      "calibration_candidate",
      "unused",
      "unused",
      "board",
      8,
      "request-2",
    ),
  ).toEqual({
    kind: "calibration_candidate",
    correspondence_id: "board",
    max_frames: 8,
    request_id: "request-2",
  });
});
it("renders completion separately from failed/not-tested checks and never claims promotion", () => {
  const job: CommissioningJob = {
    id: "a".repeat(32),
    kind: "diagnostics",
    label: "Observed diagnostics",
    status: "completed",
    max_frames: 8,
    created_at_unix: 1700000000,
    promotion_performed: false,
    logs: [{ message: "Real worker stage", at_unix: 1700000000 }],
    artifacts: [
      {
        name: "report.json",
        bytes: 512,
        url: "/api/v2/commissioning/jobs/abc/artifacts/report.json",
      },
    ],
    summary: {
      software_checks_passed: false,
      hardware_checks_passed: false,
      stages: {
        registration: { status: "failed" },
        physical_accuracy: { status: "not_tested" },
      },
    },
  };
  const html = renderToStaticMarkup(
    createElement(CommissioningResults, { job }),
  );
  expect(html).toContain("completed");
  expect(html).toContain("failed");
  expect(html).toContain("not tested");
  expect(html).toContain("Review each check separately");
  expect(html).toContain("No active map, pose or calibration promotion");
  expect(html).toContain("report.json");
  expect(html).toContain("Real worker stage");
  expect(commissioningChecks(job.summary)).toHaveLength(4);
});
it("exposes six keyboard-accessible stages and disabled submission before resources or operator access", () => {
  const html = renderToStaticMarkup(
    createElement(CommissioningPanel, {
      role: "viewer",
      calibration: null,
      onClose: () => {},
    }),
  );
  expect(html.match(/aria-pressed=/g)).toHaveLength(6);
  expect(html).toContain('aria-label="Guided commissioning"');
  expect(html).toMatch(/<button[^>]*disabled=""[^>]*type="submit"/);
  expect(html).toContain("never activates calibration, maps or poses");
  expect(html).toContain("pausing alone does not release sensors");
});
