import * as T from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import type {
  Layers,
  LayerName,
  Manifest,
  Numeric,
  SceneFrame,
  Track,
  Vec3,
  Zone,
} from "../lib/types";
import { initialLayers, palette } from "../lib/types";
import { zoneYaw } from "../lib/geometry";
import { chooseLOD, normalizeBounds } from "./assets";
import { floorPickPoint } from "./picking";
export interface RenderMetrics {
  fps: number;
  frameMs: number;
  instances: number;
  drawCalls: number;
  triangles: number;
  geometries: number;
  textures: number;
  tiles: number;
  frameMsP50: number;
  frameMsP95: number;
  frameMsP99: number;
  drawingBuffer: [number, number];
  updateMs: number;
  renderSubmitMs: number;
}
export const INSTANCE_LIMIT = 100000;
/** Instance color is independent of vertex color; BoxGeometry has no color attribute. */
export function createSemanticVoxelMesh(capacity: number) {
  const mesh = new T.InstancedMesh(
    new T.BoxGeometry(0.085, 0.085, 0.085),
    new T.MeshBasicMaterial({
      vertexColors: false,
      transparent: true,
      opacity: 0.78,
    }),
    capacity,
  );
  mesh.count = 0;
  mesh.instanceMatrix.setUsage(T.DynamicDrawUsage);
  mesh.instanceColor = new T.InstancedBufferAttribute(
    new Float32Array(capacity * 3),
    3,
  );
  mesh.frustumCulled = false;
  return mesh;
}
const edgeIndices = Array.from({ length: 8 }, (_, i) =>
  [1, 2, 4].filter((bit) => (i & bit) === 0).map((bit) => [i, i | bit]),
).flat();
function disposeObject(object: T.Object3D) {
  object.traverse((node) => {
    const mesh = node as T.Mesh;
    if (mesh.geometry) mesh.geometry.dispose();
    if (mesh.material)
      for (const material of Array.isArray(mesh.material)
        ? mesh.material
        : [mesh.material]) {
        for (const value of Object.values(material))
          if (value instanceof T.Texture) value.dispose();
        material.dispose();
      }
  });
}
function clearGroup(group: T.Group) {
  for (const child of [...group.children]) {
    group.remove(child);
    disposeObject(child);
  }
}
function floats(values: Numeric | undefined) {
  return values instanceof Float32Array
    ? values
    : new Float32Array(values ?? []);
}
export class SpatialRenderer {
  private scene = new T.Scene();
  private camera = new T.PerspectiveCamera(45, 1, 0.05, 1000);
  private renderer: T.WebGLRenderer;
  private controls: OrbitControls;
  private groups = {} as Record<LayerName, T.Group>;
  private voxelMesh: T.InstancedMesh;
  private matrix = new T.Matrix4();
  private color = new T.Color();
  private resize: ResizeObserver;
  private raf = 0;
  private disposed = false;
  private last = performance.now();
  private measured = performance.now();
  private frames = 0;
  private intervals: number[] = [];
  private frozen = false;
  private updateMs = 0;
  private renderSubmitMs = 0;
  private selected: number | null = null;
  private frame: SceneFrame | null = null;
  private pickMeshes: T.Object3D[] = [];
  private ray = new T.Raycaster();
  private pointer = new T.Vector2();
  private down = [0, 0];
  private pickMode: "start" | "goal" | null = null;
  private pickHeight: number | null = null;
  private manifest: Manifest | null = null;
  private tiles = new Map<string, { lod: string; object: T.Object3D }>();
  private pending = new Set<string>();
  private cutaway = true;
  private lodClock = 0;
  private loadGeneration = 0;
  private metricsCallback: (metrics: RenderMetrics) => void;
  constructor(
    private host: HTMLElement,
    private onPick: (id: number) => void,
    private onWorldPick: (point: Vec3, target: "start" | "goal") => void,
    onMetrics: (metrics: RenderMetrics) => void,
    private options: {
      fixedBuffer?: [number, number];
      pixelRatio?: number;
    } = {},
  ) {
    this.metricsCallback = onMetrics;
    this.renderer = new T.WebGLRenderer({
      antialias: true,
      powerPreference: "high-performance",
    });
    this.renderer.setPixelRatio(
      options.pixelRatio ?? Math.min(devicePixelRatio, 1.5),
    );
    this.renderer.setClearColor("#070d15");
    this.renderer.outputColorSpace = T.SRGBColorSpace;
    this.renderer.toneMapping = T.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.localClippingEnabled = true;
    this.renderer.domElement.setAttribute(
      "aria-label",
      "Interactive 3D lab. Drag to orbit, scroll to zoom. Select tracks from the adjacent list.",
    );
    this.renderer.domElement.tabIndex = 0;
    host.appendChild(this.renderer.domElement);
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(11, -13, 12);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.target.set(0, 0, 0.65);
    this.controls.enableDamping = !matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    this.controls.dampingFactor = 0.08;
    this.controls.minDistance = 3;
    this.controls.maxDistance = 100;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.scene.add(new T.HemisphereLight("#c1d8ff", "#25313e", 2.1));
    const sun = new T.DirectionalLight("#d9e8ff", 3);
    sun.position.set(0, -5, 12);
    this.scene.add(sun);
    const rim = new T.DirectionalLight("#557fd2", 1.4);
    rim.position.set(-8, 5, 5);
    this.scene.add(rim);
    for (const name of Object.keys(initialLayers) as LayerName[]) {
      const group = new T.Group();
      group.name = name;
      this.groups[name] = group;
      this.scene.add(group);
      group.visible = initialLayers[name];
    }
    const grid = new T.GridHelper(24, 48, "#1f3a4c", "#12212f");
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.025;
    this.scene.add(grid);
    const axes = new T.AxesHelper(0.75);
    axes.position.set(-6, -5, 0.02);
    this.scene.add(axes);
    this.voxelMesh = createSemanticVoxelMesh(INSTANCE_LIMIT);
    this.groups.voxels.add(this.voxelMesh);
    this.resize = new ResizeObserver(() => this.resizeViewport());
    this.resize.observe(host);
    this.resizeViewport();
    this.renderer.domElement.addEventListener("pointerdown", this.pointerDown);
    this.renderer.domElement.addEventListener("pointerup", this.pointerUp);
    this.animate();
  }
  private resizeViewport() {
    const rect = this.host.getBoundingClientRect();
    const [width, height] = this.options.fixedBuffer ?? [
      rect.width,
      rect.height,
    ];
    if (!width || !height) return;
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(width, height, false);
  }
  setLayers(layers: Layers) {
    for (const name of Object.keys(layers) as LayerName[])
      this.groups[name].visible = layers[name];
  }
  setCutaway(enabled: boolean) {
    this.cutaway = enabled;
    for (const { object } of this.tiles.values())
      this.applyStaticMaterial(object);
  }
  setPickMode(mode: "start" | "goal" | null, height: number | null = null) {
    this.pickHeight = height;
    this.pickMode = height !== null && Number.isFinite(height) ? mode : null;
    this.host.style.cursor = this.pickMode ? "crosshair" : "";
  }
  setSelected(id: number | null) {
    this.selected = id;
    if (this.frame) this.drawTracks(this.frame.scene.tracks ?? []);
  }
  preset(name: string) {
    const center = new T.Vector3(0, 0, 0.6);
    if (this.manifest?.bounds) {
      const [a, b] = normalizeBounds(this.manifest.bounds);
      center.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.6);
    }
    this.controls.target.copy(center);
    if (name === "top")
      this.camera.position.copy(center).add(new T.Vector3(0, -0.01, 19));
    else if (name === "sensor" && this.frame?.scene.sensor_pose_world) {
      const m = this.frame.scene.sensor_pose_world;
      this.camera.position.set(m[0][3], m[1][3] - 2, m[2][3] + 1.5);
      this.controls.target.set(m[0][3], m[1][3] + 3, m[2][3]);
    } else this.camera.position.copy(center).add(new T.Vector3(11, -13, 12));
    this.controls.update();
  }
  focusTrack(id: number) {
    const track = this.frame?.scene.tracks.find((t) => t.track_id === id);
    if (!track) return;
    this.controls.target.fromArray(track.position);
    this.camera.position.fromArray(track.position).add(new T.Vector3(4, -5, 4));
    this.controls.update();
  }
  async loadManifest(url: string) {
    const generation = ++this.loadGeneration;
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok)
      throw new Error(`Map manifest returned ${response.status}`);
    const manifest: Manifest = await response.json();
    if (
      manifest.coordinate_frame !== "world_z_up" ||
      !Array.isArray(manifest.tiles) ||
      manifest.tiles.length > 128
    )
      throw new Error("Unsupported or oversized map manifest");
    normalizeBounds(manifest.bounds);
    const ids = new Set<string>();
    for (const tile of manifest.tiles) {
      normalizeBounds(tile.bounds);
      if (
        typeof tile.id !== "string" ||
        ids.has(tile.id) ||
        !Array.isArray(tile.lods) ||
        !tile.lods.length ||
        tile.lods.length > 8
      )
        throw new Error("Invalid map tile");
      ids.add(tile.id);
      for (const lod of tile.lods)
        if (
          typeof lod.url !== "string" ||
          new URL(lod.url, location.href).origin !== location.origin ||
          !Number.isFinite(lod.distance) ||
          lod.distance < 0 ||
          !Number.isSafeInteger(lod.triangles) ||
          lod.triangles < 0
        )
          throw new Error("Invalid local map LOD");
    }
    if (this.disposed || generation !== this.loadGeneration) return;
    for (const { object } of this.tiles.values()) {
      this.groups.map.remove(object);
      disposeObject(object);
    }
    this.tiles.clear();
    this.manifest = manifest;
    this.refreshLods();
  }
  private applyStaticMaterial(object: T.Object3D) {
    const bounds = normalizeBounds(
      this.manifest?.bounds ?? [-6, -5, 0, 6, 5, 3.5],
    );
    const ceiling = bounds[0][2] + 0.82 * (bounds[1][2] - bounds[0][2]);
    const planes = this.cutaway
      ? [
          new T.Plane(new T.Vector3(0, 0, -1), ceiling),
          new T.Plane(new T.Vector3(-1, 0, 0), bounds[1][0] - 0.2),
          new T.Plane(new T.Vector3(0, 1, 0), -bounds[0][1] - 0.2),
        ]
      : [];
    object.traverse((node) => {
      if (node instanceof T.Mesh) {
        for (const material of Array.isArray(node.material)
          ? node.material
          : [node.material]) {
          material.clippingPlanes = planes;
          material.side = T.DoubleSide;
          if (material instanceof T.MeshStandardMaterial) {
            material.roughness = 0.82;
            material.metalness = 0.08;
          }
          material.needsUpdate = true;
        }
      }
    });
  }
  private refreshLods() {
    if (!this.manifest || this.pending.size >= 2) return;
    for (const tile of this.manifest.tiles) {
      if (this.pending.size >= 2) break;
      const bounds = normalizeBounds(tile.bounds);
      const center = new T.Vector3(...bounds[0])
        .add(new T.Vector3(...bounds[1]))
        .multiplyScalar(0.5);
      const distance = center.distanceTo(this.camera.position);
      const lod = chooseLOD(tile.lods, distance);
      if (
        !lod ||
        this.tiles.get(tile.id)?.lod === lod.url ||
        this.pending.has(tile.id)
      )
        continue;
      this.pending.add(tile.id);
      const generation = this.loadGeneration;
      void new GLTFLoader()
        .loadAsync(lod.url)
        .then((gltf) => {
          if (this.disposed || generation !== this.loadGeneration) {
            disposeObject(gltf.scene);
            return;
          }
          const previous = this.tiles.get(tile.id);
          if (previous) {
            this.groups.map.remove(previous.object);
            disposeObject(previous.object);
          }
          this.applyStaticMaterial(gltf.scene);
          this.groups.map.add(gltf.scene);
          this.tiles.set(tile.id, { lod: lod.url, object: gltf.scene });
        })
        .catch((error) =>
          this.host.dispatchEvent(
            new CustomEvent("asseterror", { detail: String(error) }),
          ),
        )
        .finally(() => this.pending.delete(tile.id));
    }
  }
  updateFrame(frame: SceneFrame) {
    const began = performance.now();
    this.frame = frame;
    const a = frame.arrays;
    const centers = a["voxels.centers"],
      colors = a["voxels.colors"];
    const count = Math.min(
      INSTANCE_LIMIT,
      Math.floor((centers?.length ?? 0) / 3),
    );
    for (let i = 0; i < count; i++) {
      this.matrix.makeTranslation(
        centers[i * 3],
        centers[i * 3 + 1],
        centers[i * 3 + 2],
      );
      this.voxelMesh.setMatrixAt(i, this.matrix);
      this.color.setRGB(
        colors?.[i * 3] ?? 0.2,
        colors?.[i * 3 + 1] ?? 0.7,
        colors?.[i * 3 + 2] ?? 0.9,
      );
      this.voxelMesh.setColorAt(i, this.color);
    }
    this.voxelMesh.count = count;
    this.voxelMesh.instanceMatrix.needsUpdate = true;
    this.voxelMesh.instanceColor!.needsUpdate = true;
    this.drawFlow(centers, a["voxels.flow"]);
    this.drawTracks(frame.scene.tracks ?? []);
    this.drawZones(frame.scene.geofences ?? []);
    this.drawPath(a.path);
    this.drawRisk(a);
    this.drawTraffic(frame);
    this.drawRGBD(a.gemini_inlay);
    this.updateMs = performance.now() - began;
  }
  private line(
    group: T.Group,
    positions: number[] | Float32Array,
    color: string,
    opacity = 1,
    dashed = false,
  ) {
    if (!positions.length) return;
    const geometry = new T.BufferGeometry();
    geometry.setAttribute(
      "position",
      new T.Float32BufferAttribute(positions, 3),
    );
    const line = new T.LineSegments(
      geometry,
      dashed
        ? new T.LineDashedMaterial({
            color,
            transparent: true,
            opacity,
            dashSize: 0.11,
            gapSize: 0.065,
          })
        : new T.LineBasicMaterial({ color, transparent: opacity < 1, opacity }),
    );
    if (dashed) line.computeLineDistances();
    group.add(line);
  }
  private drawFlow(centers: Numeric | undefined, flow: Numeric | undefined) {
    const group = this.groups.flow;
    clearGroup(group);
    if (!centers || !flow || centers.length !== flow.length) return;
    const points: number[] = [];
    const step = Math.max(1, Math.ceil(centers.length / 3 / 800));
    for (let i = 0; i < centers.length; i += 3 * step) {
      if (Math.hypot(flow[i], flow[i + 1], flow[i + 2]) < 0.015) continue;
      points.push(
        centers[i],
        centers[i + 1],
        centers[i + 2],
        centers[i] + flow[i],
        centers[i + 1] + flow[i + 1],
        centers[i + 2] + flow[i + 2],
      );
    }
    this.line(group, points, "#ddd68b", 0.85);
  }
  private drawTracks(tracks: Track[]) {
    const group = this.groups.tracks;
    clearGroup(group);
    this.pickMeshes = [];
    for (const track of tracks.slice(0, 256)) {
      const color = track.breached_geofences?.length
        ? "#ff6374"
        : (palette[track.semantic_class] ?? "#a7cae5");
      const corners = track.obb_corners;
      if (corners?.length === 8)
        this.line(
          group,
          edgeIndices.flatMap(([i, j]) => [...corners[i], ...corners[j]]),
          track.track_id === this.selected ? "#f4f8ff" : color,
          track.state_kind === "predicted" ? 0.55 : 0.9,
          track.state_kind === "predicted",
        );
      const bounds = new T.Box3().setFromPoints(
        (corners ?? []).map((v) => new T.Vector3(...v)),
      );
      if (bounds.isEmpty()) continue;
      const size = bounds.getSize(new T.Vector3());
      const pick = new T.Mesh(
        new T.BoxGeometry(
          Math.max(size.x, 0.15),
          Math.max(size.y, 0.15),
          Math.max(size.z, 0.15),
        ),
        new T.MeshBasicMaterial({
          transparent: true,
          opacity: 0.025,
          depthWrite: false,
          color,
        }),
      );
      pick.position.copy(bounds.getCenter(new T.Vector3()));
      pick.userData.trackId = track.track_id;
      group.add(pick);
      this.pickMeshes.push(pick);
      const marker = new T.Mesh(
        new T.SphereGeometry(
          track.track_id === this.selected ? 0.09 : 0.06,
          8,
          6,
        ),
        new T.MeshBasicMaterial({ color }),
      );
      marker.position.fromArray(track.position);
      group.add(marker);
      const trajectory = track.predicted_trajectory ?? [];
      if (trajectory.length > 1)
        this.line(
          group,
          trajectory
            .slice(0, -1)
            .flatMap((p, i) => [...p, ...trajectory[i + 1]]),
          color,
          0.55,
        );
    }
  }
  private drawZones(zones: Zone[]) {
    const group = this.groups.geofences;
    clearGroup(group);
    for (const zone of zones.slice(0, 64)) {
      const geometry = new T.BoxGeometry(
        ...(zone.half_extents.map((v) => v * 2) as Vec3),
      );
      const color = zone.is_breached ? "#ff6374" : "#e4af57";
      const fill = new T.Mesh(
        geometry,
        new T.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: 0.055,
          depthWrite: false,
        }),
      );
      fill.position.fromArray(zone.center);
      if (zone.rotation_matrix) {
        const r = zone.rotation_matrix;
        fill.setRotationFromMatrix(
          new T.Matrix4().set(
            r[0][0],
            r[0][1],
            r[0][2],
            0,
            r[1][0],
            r[1][1],
            r[1][2],
            0,
            r[2][0],
            r[2][1],
            r[2][2],
            0,
            0,
            0,
            0,
            1,
          ),
        );
      } else fill.rotation.z = zoneYaw(zone);
      const edge = new T.LineSegments(
        new T.EdgesGeometry(geometry),
        new T.LineBasicMaterial({ color, transparent: true, opacity: 0.85 }),
      );
      edge.position.copy(fill.position);
      edge.quaternion.copy(fill.quaternion);
      group.add(fill, edge);
    }
  }
  private drawPath(path: Numeric | undefined) {
    const group = this.groups.path;
    clearGroup(group);
    if (!path || path.length < 6) return;
    const geometry = new T.BufferGeometry();
    geometry.setAttribute("position", new T.BufferAttribute(floats(path), 3));
    group.add(
      new T.Line(
        geometry,
        new T.LineDashedMaterial({
          color: "#47f4bf",
          dashSize: 0.25,
          gapSize: 0.12,
          scale: 1,
        }),
      ),
    );
    (group.children[0] as T.Line).computeLineDistances();
  }
  private drawRisk(arrays: Record<string, Numeric>) {
    const group = this.groups.risk;
    clearGroup(group);
    const centers = arrays["floor_risk.centers"],
      risk = arrays["floor_risk.risk"],
      colors = arrays["floor_risk.colors"];
    if (!centers || !risk) return;
    const p: number[] = [],
      c: number[] = [];
    for (let i = 0; i < Math.min(risk.length, 20000); i++)
      if (risk[i] > 0.15) {
        p.push(centers[i * 3], centers[i * 3 + 1], centers[i * 3 + 2] + 0.015);
        c.push(
          colors?.[i * 3] ?? risk[i],
          colors?.[i * 3 + 1] ?? 0.35,
          colors?.[i * 3 + 2] ?? 0.1,
        );
      }
    if (!p.length) return;
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.Float32BufferAttribute(p, 3));
    geo.setAttribute("color", new T.Float32BufferAttribute(c, 3));
    group.add(
      new T.Points(
        geo,
        new T.PointsMaterial({
          vertexColors: true,
          size: 0.115,
          transparent: true,
          opacity: 0.68,
          depthWrite: false,
        }),
      ),
    );
  }
  private drawTraffic(frame: SceneFrame) {
    const group = this.groups.traffic;
    clearGroup(group);
    const values = frame.arrays["traffic_heatmap.values"];
    const traffic = frame.scene.traffic_heatmap;
    const bounds =
      traffic?.bounds_xy ?? frame.scene.diagnostics.traffic_grid?.bounds_xy;
    const shape = traffic?.shape ?? traffic?.source_shape;
    if (
      !values?.length ||
      !bounds ||
      !shape ||
      shape[0] * shape[1] !== values.length
    )
      return;
    let peak = 0;
    for (const value of values) peak = Math.max(peak, value);
    if (!peak) return;
    const positions: number[] = [],
      colors: number[] = [];
    for (let i = 0; i < values.length; i++) {
      if (values[i] < peak * 0.025) continue;
      const y = Math.floor(i / shape[1]),
        x = i % shape[1];
      positions.push(
        bounds[0] + ((x + 0.5) / shape[1]) * (bounds[1] - bounds[0]),
        bounds[2] + ((y + 0.5) / shape[0]) * (bounds[3] - bounds[2]),
        0.045,
      );
      const t = values[i] / peak;
      colors.push(0.4 + 0.6 * t, 0.15 + 0.4 * t, 0.55 - 0.4 * t);
    }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.Float32BufferAttribute(positions, 3));
    geo.setAttribute("color", new T.Float32BufferAttribute(colors, 3));
    group.add(
      new T.Points(
        geo,
        new T.PointsMaterial({
          vertexColors: true,
          size: 0.19,
          transparent: true,
          opacity: 0.6,
          depthWrite: false,
        }),
      ),
    );
  }
  private drawRGBD(values: Numeric | undefined) {
    const group = this.groups.rgbd;
    clearGroup(group);
    if (!values) return;
    const count = Math.min(Math.floor(values.length / 6), 20000),
      p = new Float32Array(count * 3),
      c = new Float32Array(count * 3);
    for (let i = 0; i < count; i++)
      for (let j = 0; j < 3; j++) {
        p[i * 3 + j] = values[i * 6 + j];
        c[i * 3 + j] =
          values[i * 6 + j + 3] > 1
            ? values[i * 6 + j + 3] / 255
            : values[i * 6 + j + 3];
      }
    const geo = new T.BufferGeometry();
    geo.setAttribute("position", new T.BufferAttribute(p, 3));
    geo.setAttribute("color", new T.BufferAttribute(c, 3));
    group.add(
      new T.Points(
        geo,
        new T.PointsMaterial({ vertexColors: true, size: 0.035 }),
      ),
    );
  }
  private pointerDown = (event: PointerEvent) => {
    this.down = [event.clientX, event.clientY];
  };
  private pointerUp = (event: PointerEvent) => {
    if (
      Math.hypot(event.clientX - this.down[0], event.clientY - this.down[1]) > 5
    )
      return;
    const r = this.renderer.domElement.getBoundingClientRect();
    this.pointer.set(
      ((event.clientX - r.left) / r.width) * 2 - 1,
      (-(event.clientY - r.top) / r.height) * 2 + 1,
    );
    this.ray.setFromCamera(this.pointer, this.camera);
    if (this.pickMode) {
      const point = floorPickPoint(this.ray.ray, this.pickHeight);
      if (point) this.onWorldPick(point, this.pickMode);
      return;
    }
    if (!this.groups.tracks.visible) return;
    const hit = this.ray.intersectObjects(this.pickMeshes, false)[0];
    if (hit) this.onPick(hit.object.userData.trackId);
  };
  private animate = () => {
    if (this.disposed || this.frozen) return;
    this.raf = requestAnimationFrame(this.animate);
    const now = performance.now();
    if (this.frames || this.intervals.length)
      this.intervals.push(now - this.last);
    if (this.intervals.length > 1200) this.intervals.shift();
    this.controls.update();
    const renderStart = performance.now();
    this.renderer.render(this.scene, this.camera);
    this.renderSubmitMs = performance.now() - renderStart;
    this.frames++;
    if (now - this.lodClock > 1000) {
      this.lodClock = now;
      this.refreshLods();
    }
    if (now - this.measured > 1000) {
      const ordered = [...this.intervals].sort((a, b) => a - b);
      const percentile = (p: number) =>
        ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * p))] ??
        0;
      const buffer = this.renderer.getDrawingBufferSize(new T.Vector2());
      this.metricsCallback({
        fps: (this.frames * 1000) / (now - this.measured),
        frameMs: (now - this.measured) / this.frames,
        instances: this.voxelMesh.count,
        drawCalls: this.renderer.info.render.calls,
        triangles: this.renderer.info.render.triangles,
        geometries: this.renderer.info.memory.geometries,
        textures: this.renderer.info.memory.textures,
        tiles: this.tiles.size,
        frameMsP50: percentile(0.5),
        frameMsP95: percentile(0.95),
        frameMsP99: percentile(0.99),
        drawingBuffer: [buffer.x, buffer.y],
        updateMs: this.updateMs,
        renderSubmitMs: this.renderSubmitMs,
      });
      this.frames = 0;
      this.measured = now;
    }
    this.last = now;
  };
  deviceInfo() {
    const gl = this.renderer.getContext();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      vendor: debug
        ? String(gl.getParameter(debug.UNMASKED_VENDOR_WEBGL))
        : "Not exposed",
      renderer: debug
        ? String(gl.getParameter(debug.UNMASKED_RENDERER_WEBGL))
        : "Not exposed",
      webgl: gl.getParameter(gl.VERSION),
    };
  }
  freeze() {
    this.frozen = true;
    cancelAnimationFrame(this.raf);
  }
  dispose() {
    this.disposed = true;
    this.loadGeneration++;
    cancelAnimationFrame(this.raf);
    this.resize.disconnect();
    this.controls.dispose();
    this.renderer.domElement.removeEventListener(
      "pointerdown",
      this.pointerDown,
    );
    this.renderer.domElement.removeEventListener("pointerup", this.pointerUp);
    disposeObject(this.scene);
    this.renderer.dispose();
    this.renderer.forceContextLoss();
    this.renderer.domElement.remove();
    this.tiles.clear();
  }
}
