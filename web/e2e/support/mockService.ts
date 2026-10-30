import type { Page, WebSocketRoute } from "@playwright/test";
import type { Bootstrap, Metadata, Track } from "../../src/lib/types";
import { packet, type Part } from "../../tests/fixtures";

/**
 * A deterministic in-browser stand-in for the Python command-center service.
 * HTTP endpoints are answered with `page.route`; the scene stream is a real
 * binary protocol-v2 WebSocket so the worker decoder path is exercised.
 */
export type Role = "operator" | "viewer";
export interface MockOptions {
  role?: Role;
  /** Answer bootstrap with 401 until a session credential is posted. */
  requireLogin?: boolean;
  /** Milliseconds between streamed keyframes. */
  frameInterval?: number;
  /** Publish a measured-map manifest with this many tiles; their GLBs answer 404. */
  mapTiles?: number;
}
export interface CommandRecord {
  action: string;
  payload: unknown;
  expected_revision: number;
}

const capabilities = {
  pause: true,
  resume: true,
  step: true,
  seek: false,
  set_start_goal: true,
  set_geofences: true,
  select_source: true,
};

function tracks(frame: number): Track[] {
  const box = (x: number, y: number, w: number, d: number, h: number) =>
    [0, 1].flatMap((i) =>
      [0, 1].flatMap((j) =>
        [0, 1].map(
          (k) => [x + i * w, y + j * d, k * h] as [number, number, number],
        ),
      ),
    );
  const t = frame * 0.05;
  const person = { x: -1.5 + Math.sin(t) * 0.8, y: 1 };
  return [
    {
      track_id: 1,
      semantic_class: 2,
      semantic_name: "PERSON",
      class_probs: [0, 0, 0.94, 0.03, 0.03, 0, 0],
      confidence: 0.94,
      position: [person.x + 0.25, person.y + 0.25, 0.9],
      velocity: [0.4, 0, 0],
      obb_corners: box(person.x, person.y, 0.5, 0.5, 1.8),
      predicted_trajectory: [
        [person.x + 0.25, person.y + 0.25, 0.9],
        [person.x + 0.65, person.y + 0.25, 0.9],
      ],
      ttc_seconds: 4.2,
      breached_geofences: ["Bench"],
      eigen_invariants: Array.from({ length: 10 }, (_, i) => 0.05 * i),
      state_kind: "measurement_updated",
      measurement_age_seconds: 0,
    },
    {
      track_id: 3,
      semantic_class: 3,
      semantic_name: "DYNAMIC_ROBOT_CART",
      class_probs: [0, 0, 0.02, 0.95, 0.03, 0, 0],
      confidence: 0.95,
      position: [2.4, -1.2, 0.4],
      velocity: [0.1, 0, 0],
      obb_corners: box(2, -1.6, 0.8, 0.8, 0.8),
      predicted_trajectory: [],
      ttc_seconds: null,
      breached_geofences: [],
      state_kind: "predicted",
      measurement_age_seconds: 0.4,
    },
  ];
}

export function mockScene(frame: number): Metadata {
  return {
    schema_version: 1,
    frame_id: frame,
    timestamp: frame * 0.1,
    fps: 9.5,
    coordinate_frame: "world_z_up",
    source_mode: "sim",
    processed_at_unix: Date.now() / 1000,
    tracks: tracks(frame),
    geofences: [
      {
        name: "Bench",
        center: [-1.2, 1.2, 0.75],
        half_extents: [1, 0.6, 0.75],
        yaw: 0,
        severity_level: 2,
        is_breached: true,
        breaching_track_ids: [1],
      },
    ],
    alerts: frame > 3 ? ["WARNING: person inside protected zone Bench"] : [],
    trajectory_status: "ok",
    sensor_pose_world: [
      [1, 0, 0, 0],
      [0, 1, 0, -3],
      [0, 0, 1, 1.4],
      [0, 0, 0, 1],
    ],
    identity: {
      lab_id: "lab-e2e",
      rig_id: "rig-e2e",
      map_revision: "mockrevision0001",
      calibration_id: "simulation-v1",
    },
    validity: { observations: true, localization: true },
    diagnostics: {
      source_mode: "sim",
      runtime_session_id: "e2e-session",
      processing_ms: 120 + (frame % 5) * 7,
      end_to_end_fps: 8.2,
      memory_mb: 812,
      queue_depth: 0,
      observation_counts: { lidar: 24000, depth: 9000 },
      robot_position: [-4, -3, 0.3],
      module_ms: {
        deskew: 4.1,
        occupancy: 18.4,
        perception_tracking: 61.2,
        planning: 9.8,
      },
    },
  };
}

/** A small room: floor ring, two walls and a pillar, as semantic voxels. */
function voxelParts(frame: number): Part[] {
  const keys: number[] = [],
    colors: number[] = [],
    flow: number[] = [];
  const add = (x: number, y: number, z: number, rgb: number[], f = [0, 0, 0]) => {
    keys.push(x, y, z);
    colors.push(...rgb);
    flow.push(...f);
  };
  for (let x = -25; x <= 25; x++)
    for (let y = -20; y <= 20; y++)
      if ((x + y) % 2 === 0 && x * x + y * y < 600)
        add(x, y, 0, [0.19, 0.3, 0.36]);
  for (let x = -25; x <= 25; x++)
    for (let z = 1; z < 14; z++) add(x, 20, z, [0.14, 0.72, 0.85]);
  for (let y = -20; y < 20; y++)
    for (let z = 1; z < 14; z++) add(-25, y, z, [0.14, 0.72, 0.85]);
  const px = Math.round(-8 + Math.sin(frame * 0.05) * 4);
  for (let z = 1; z < 10; z++)
    for (let d = 0; d < 4; d++)
      add(px + (d % 2), 5 + (d >> 1), z, [0.13, 0.9, 0.69], [0.04, 0, 0]);
  const scale = 0.18;
  const centers = keys.map((v) => v * scale);
  const n = keys.length / 3;
  return [
    { name: "voxels.keys", data: new Int32Array(keys), shape: [n, 3] },
    { name: "voxels.centers", data: new Float32Array(centers), shape: [n, 3] },
    { name: "voxels.colors", data: new Float32Array(colors), shape: [n, 3] },
    { name: "voxels.flow", data: new Float32Array(flow), shape: [n, 3] },
    {
      name: "path",
      data: new Float32Array([-4, -3, 0.3, -1, -2, 0.3, 1.5, 0, 0.3, 3, 2.5, 0.3]),
      shape: [4, 3],
    },
  ];
}

export class MockService {
  readonly commands: CommandRecord[] = [];
  readonly sockets: WebSocketRoute[] = [];
  private frame = 1;
  private revision = 0;
  private paused = false;
  private history: Array<Record<string, unknown>> = [];
  private signedIn: boolean;
  private timers: ReturnType<typeof setInterval>[] = [];
  constructor(
    private page: Page,
    private options: MockOptions = {},
  ) {
    this.signedIn = !options.requireLogin;
  }

  bootstrap(includeLatest = true): Bootstrap {
    const role = this.options.role ?? "operator";
    return {
      protocol_version: 2,
      identity: mockScene(this.frame).identity!,
      source_mode: "sim",
      hardware_status: "not_tested",
      map_revision: "mockrevision0001",
      map_manifest_url: (this.options.mapTiles
        ? "/api/v2/assets/manifest.json"
        : null) as unknown as string,
      stream_url: "/ws/v2/scene",
      role,
      control_revision: this.revision,
      capabilities: Object.fromEntries(
        Object.entries(capabilities).map(([k, v]) => [k, role === "operator" && v]),
      ),
      latest: includeLatest ? mockScene(this.frame) : null,
      paused: this.paused,
      error: null,
      available_sources: [
        { id: "simulation", source: "sim", label: "Simulation" },
        { id: "sample", source: "replay", recording_id: "sample", label: "Sample recording" },
      ],
      replay: { frame_count: undefined, current_frame: 0 },
    };
  }

  async install() {
    const json = (body: unknown, status = 200) => ({
      status,
      contentType: "application/json",
      body: JSON.stringify(body),
    });
    await this.page.route("**/api/v2/**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const path = url.pathname;
      if (path === "/api/v2/session" && request.method() === "POST") {
        const token = request.postDataJSON()?.token;
        if (token !== "e2e-operator-credential-0000000000000")
          return route.fulfill(json({ detail: "Invalid access credential" }, 401));
        this.signedIn = true;
        return route.fulfill(json({ role: "operator", expires_in: 3600 }));
      }
      if (!this.signedIn)
        return route.fulfill(json({ detail: "Sign in to this private command center" }, 401));
      if (path === "/api/v2/bootstrap")
        return route.fulfill(
          json(this.bootstrap(url.searchParams.get("include_latest") !== "false")),
        );
      if (path === "/api/v2/state") return route.fulfill(json(mockScene(this.frame)));
      if (path === "/api/v2/assets/manifest.json" && this.options.mapTiles)
        return route.fulfill(
          json({
            coordinate_frame: "world_z_up",
            bounds: [-6, -5, 0, 6, 5, 3.5],
            tiles: Array.from({ length: this.options.mapTiles }, (_, i) => ({
              id: `tile-${i}`,
              bounds: [i - 6, -5, 0, i - 5, 5, 3.5],
              lods: [{ url: `/api/v2/assets/tile-${i}.glb`, distance: 0, triangles: 12 }],
            })),
          }),
        );
      if (path.startsWith("/api/v2/assets/tile-"))
        return route.fulfill(json({ detail: "Not found" }, 404));
      if (path === "/api/v2/commands" && request.method() === "GET")
        return route.fulfill(
          json({ revision: this.revision, pending: 0, history: this.history }),
        );
      if (path === "/api/v2/commands" && request.method() === "POST") {
        const body = request.postDataJSON();
        this.commands.push({
          action: body.action,
          payload: body.payload,
          expected_revision: body.expected_revision,
        });
        this.revision += 1;
        if (body.action === "pause") this.paused = true;
        if (body.action === "resume") this.paused = false;
        this.history = [
          ...this.history,
          {
            request_id: body.request_id,
            action: body.action,
            status: "applied",
            at_unix: Date.now() / 1000,
          },
        ];
        return route.fulfill(
          json(
            { request_id: body.request_id, status: "accepted", revision: this.revision },
            202,
          ),
        );
      }
      if (path === "/api/v2/trajectory.csv")
        return route.fulfill({
          status: 200,
          contentType: "text/csv",
          body: "t,x,y,z\n0,-4,-3,0.3\n",
        });
      return route.fulfill(json({ detail: "Unknown API endpoint" }, 404));
    });
    await this.page.routeWebSocket("**/ws/v2/scene", (ws) => {
      if (!this.signedIn) {
        ws.close({ code: 4401, reason: "Authentication required" });
        return;
      }
      this.sockets.push(ws);
      let sequence = 0;
      const send = () => {
        if (!this.paused) this.frame += 1;
        sequence += 1;
        const buffer = packet(voxelParts(this.frame), {
          kind: "keyframe",
          sequence,
          base_sequence: null,
          stream_id: "e2e-stream",
          scene: mockScene(this.frame),
        });
        ws.send(Buffer.from(buffer));
      };
      send();
      const timer = setInterval(send, this.options.frameInterval ?? 250);
      this.timers.push(timer);
      ws.onClose(() => clearInterval(timer));
    });
  }

  dispose() {
    for (const timer of this.timers) clearInterval(timer);
  }
}
