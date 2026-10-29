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
import { GrowableGeometry } from "./buffers";
import type { CinematicEffects } from "./effects";
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
  quality: ResolvedQuality;
  qualitySetting: Quality;
  pixelRatio: number;
  renderMode: "continuous" | "on-demand";
  /** Presentation-only effects currently drawn: post-processing and decorative motion. */
  effects: { postprocessing: boolean; motion: boolean };
}
/** Measured-map tile loading, emitted as a `tileprogress` event on the host. */
export interface MapProgress {
  loaded: number;
  total: number;
  failed: number;
}
export type Quality = "auto" | "performance" | "balanced" | "cinematic";
export type ResolvedQuality = Exclude<Quality, "auto">;
/**
 * Pixel-ratio ceilings per tier; adaptive scaling works between
 * MIN_PIXEL_RATIO and these. `auto` starts at balanced.
 */
const PIXEL_RATIO_CAP: Record<ResolvedQuality, () => number> = {
  performance: () => Math.min(devicePixelRatio, 1),
  balanced: () => Math.min(devicePixelRatio, 1.5),
  cinematic: () => Math.min(devicePixelRatio, 1.5),
};
const MIN_PIXEL_RATIO = 0.6;
export const INSTANCE_LIMIT = 100000;
const MAX_TRACKS = 256;
type Overlay<O extends T.Object3D = T.Object3D> = { buffer: GrowableGeometry; object: O };
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
type Uniform<V> = { value: V };
/**
 * Decorative pulse for the cinematic tier: a slow ring (1.2 m/s, every 10 s)
 * from the sensor pose. It is presentation only and deliberately unlike the
 * real LiDAR scan rate or phase.
 */
function decoratePulse(
  material: T.Material,
  uniforms: { time: Uniform<number>; origin: Uniform<T.Vector3>; strength: Uniform<number> },
) {
  material.onBeforeCompile = (shader) => {
    Object.assign(shader.uniforms, {
      uPulseTime: uniforms.time,
      uPulseOrigin: uniforms.origin,
      uPulseStrength: uniforms.strength,
    });
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nvarying vec3 vPulseWorld;")
      .replace(
        "#include <project_vertex>",
        `#include <project_vertex>
#ifdef USE_INSTANCING
  vPulseWorld = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;
#else
  vPulseWorld = (modelMatrix * vec4(transformed, 1.0)).xyz;
#endif`,
      );
    shader.fragmentShader = shader.fragmentShader
      .replace(
        "#include <common>",
        `#include <common>
varying vec3 vPulseWorld;
uniform float uPulseTime;
uniform vec3 uPulseOrigin;
uniform float uPulseStrength;`,
      )
      .replace(
        "#include <dithering_fragment>",
        `#include <dithering_fragment>
  float pulseRadius = mod(uPulseTime * 1.2, 12.0);
  float pulse = exp(-pow((distance(vPulseWorld.xy, uPulseOrigin.xy) - pulseRadius) * 2.5, 2.0));
  gl_FragColor.rgb += uPulseStrength * pulse * vec3(0.16, 0.42, 0.5);`,
      );
  };
  material.customProgramCacheKey = () => "decorative-pulse";
}
/** Animated dashes: shift the dash pattern along the line. */
function decorateDashOffset(material: T.LineDashedMaterial, offset: Uniform<number>) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uDashOffset = offset;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform float uDashOffset;")
      .replace(
        "vLineDistance = scale * lineDistance;",
        "vLineDistance = scale * lineDistance - uDashOffset;",
      );
  };
  material.customProgramCacheKey = () => "dash-offset";
}
/** Floor rings under tracks breathe in and out, per instance. */
function decorateRings(material: T.Material, time: Uniform<number>) {
  material.onBeforeCompile = (shader) => {
    shader.uniforms.uPulseTime = time;
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", "#include <common>\nuniform float uPulseTime;")
      .replace(
        "#include <begin_vertex>",
        `#include <begin_vertex>
  transformed.xy *= 1.0 + 0.18 * sin(uPulseTime * 2.4 + float(gl_InstanceID) * 1.7);`,
      );
  };
  material.customProgramCacheKey = () => "breathing-rings";
}
const easeInOutCubic = (t: number) =>
  t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
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
export class SpatialRenderer {
  private scene = new T.Scene();
  private camera = new T.PerspectiveCamera(45, 1, 0.05, 1000);
  private renderer: T.WebGLRenderer;
  private controls: OrbitControls;
  private groups = {} as Record<LayerName, T.Group>;
  private voxelMesh: T.InstancedMesh;
  // Scratch objects and caches reused by every frame update.
  private colors = new Map<string, T.Color>();
  private box = new T.Box3();
  private vector = new T.Vector3();
  private center = new T.Vector3();
  private scale = new T.Vector3();
  private identity = new T.Quaternion();
  private instance = new T.Matrix4();
  private pickIds: number[] = [];
  private zoneKey = "";
  private flowLines!: Overlay;
  private trackEdges!: Overlay;
  private predictedEdges!: Overlay;
  private trackPaths!: Overlay;
  private pathLine!: Overlay;
  private riskPoints!: Overlay;
  private trafficPoints!: Overlay;
  private rgbdPoints!: Overlay;
  private pickMesh: T.InstancedMesh;
  private markers: T.InstancedMesh;
  private dirty = true;
  private quality: Quality = "auto";
  private resolved: ResolvedQuality = "balanced";
  private slowWindows = 0;
  private headroomWindows = 0;
  private autoDemoted = false;
  private overBudgetWindows = 0;
  private fastWindows = 0;
  private skipInterval = true;
  private onscreen = true;
  private visibility: IntersectionObserver;
  private effects: CinematicEffects | null = null;
  private effectsLoading = false;
  private reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
  private fog = new T.FogExp2("#070d15", 0.028);
  private epoch = performance.now();
  private decorative = {
    time: { value: 0 },
    origin: { value: new T.Vector3() },
    strength: { value: 0 },
  };
  private dashOffset = { value: 0 };
  private rings: T.InstancedMesh;
  private breachedFills: T.MeshBasicMaterial[] = [];
  private introduced = false;
  private flight: {
    fromPosition: T.Vector3;
    fromTarget: T.Vector3;
    toPosition: T.Vector3;
    toTarget: T.Vector3;
    start: number;
    duration: number;
  } | null = null;
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
  private ray = new T.Raycaster();
  private pointer = new T.Vector2();
  private down = [0, 0];
  private pickMode: "start" | "goal" | null = null;
  private pickHeight: number | null = null;
  private manifest: Manifest | null = null;
  private tiles = new Map<string, { lod: string; object: T.Object3D }>();
  private pending = new Set<string>();
  private failedTiles = new Set<string>();
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
      quality?: Quality;
    } = {},
  ) {
    this.metricsCallback = onMetrics;
    this.renderer = new T.WebGLRenderer({
      antialias: true,
      powerPreference: "high-performance",
    });
    this.quality = options.quality ?? "auto";
    this.resolved = this.quality === "auto" ? "balanced" : this.quality;
    this.renderer.setPixelRatio(this.pixelRatioCap());
    this.applyTier();
    this.renderer.setClearColor("#070d15");
    this.renderer.outputColorSpace = T.SRGBColorSpace;
    this.renderer.toneMapping = T.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.localClippingEnabled = true;
    // Post-processing renders several passes per frame; count the whole frame.
    this.renderer.info.autoReset = false;
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
    const g = this.groups,
      lines = (opacity: number) =>
        new T.LineBasicMaterial({ vertexColors: true, transparent: true, opacity }),
      points = (size: number, opacity?: number) =>
        new T.PointsMaterial({
          vertexColors: true,
          size,
          ...(opacity === undefined
            ? {}
            : { transparent: true, opacity, depthWrite: false }),
        });
    this.flowLines = this.overlay(g.flow, { position: 3 }, 1600, (geometry) =>
      new T.LineSegments(
        geometry,
        new T.LineBasicMaterial({ color: "#ddd68b", transparent: true, opacity: 0.85 }),
      ),
    );
    this.trackEdges = this.overlay(g.tracks, { position: 3, color: 3 }, 1024, (geometry) =>
      new T.LineSegments(geometry, lines(0.9)),
    );
    this.predictedEdges = this.overlay(
      g.tracks,
      { position: 3, color: 3, lineDistance: 1 },
      256,
      (geometry) =>
        new T.LineSegments(
          geometry,
          new T.LineDashedMaterial({
            vertexColors: true,
            transparent: true,
            opacity: 0.55,
            dashSize: 0.11,
            gapSize: 0.065,
          }),
        ),
    );
    this.trackPaths = this.overlay(g.tracks, { position: 3, color: 3 }, 512, (geometry) =>
      new T.LineSegments(geometry, lines(0.55)),
    );
    this.pathLine = this.overlay(g.path, { position: 3, lineDistance: 1 }, 256, (geometry) =>
      new T.Line(
        geometry,
        new T.LineDashedMaterial({ color: "#47f4bf", dashSize: 0.25, gapSize: 0.12, scale: 1 }),
      ),
    );
    this.riskPoints = this.overlay(g.risk, { position: 3, color: 3 }, 20000, (geometry) =>
      new T.Points(geometry, points(0.115, 0.68)),
    );
    this.trafficPoints = this.overlay(g.traffic, { position: 3, color: 3 }, 4096, (geometry) =>
      new T.Points(geometry, points(0.19, 0.6)),
    );
    this.rgbdPoints = this.overlay(g.rgbd, { position: 3, color: 3 }, 20000, (geometry) =>
      new T.Points(geometry, points(0.035)),
    );
    // Track selection volumes (nearly invisible, raycast by instance) and markers.
    this.pickMesh = new T.InstancedMesh(
      new T.BoxGeometry(1, 1, 1),
      new T.MeshBasicMaterial({ transparent: true, opacity: 0.025, depthWrite: false }),
      MAX_TRACKS,
    );
    this.markers = new T.InstancedMesh(
      new T.SphereGeometry(0.06, 8, 6),
      new T.MeshBasicMaterial(),
      MAX_TRACKS,
    );
    decoratePulse(this.voxelMesh.material as T.Material, this.decorative);
    decorateDashOffset(
      (this.pathLine.object as T.Line).material as T.LineDashedMaterial,
      this.dashOffset,
    );
    const ringMaterial = new T.MeshBasicMaterial({
      transparent: true,
      opacity: 0.45,
      depthWrite: false,
      blending: T.AdditiveBlending,
    });
    decorateRings(ringMaterial, this.decorative.time);
    this.rings = new T.InstancedMesh(
      new T.RingGeometry(0.22, 0.28, 40),
      ringMaterial,
      MAX_TRACKS,
    );
    for (const mesh of [this.pickMesh, this.markers, this.rings]) {
      mesh.count = 0;
      mesh.visible = false;
      mesh.frustumCulled = false;
      mesh.instanceMatrix.setUsage(T.DynamicDrawUsage);
      mesh.setColorAt(0, this.colorOf("#ffffff"));
      g.tracks.add(mesh);
    }
    this.resize = new ResizeObserver(() => this.resizeViewport());
    this.resize.observe(host);
    this.visibility = new IntersectionObserver(([entry]) => {
      this.onscreen = entry?.isIntersecting ?? true;
      this.markDirty();
    });
    this.visibility.observe(host);
    this.controls.addEventListener("change", this.invalidate);
    // User input takes over from any camera flight.
    this.controls.addEventListener("start", this.cancelFlight);
    this.reducedMotion.addEventListener("change", this.invalidate);
    document.addEventListener("visibilitychange", this.markDirty);
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
    this.effects?.setSize(width, height, this.renderer.getPixelRatio());
    this.invalidate();
  }
  setLayers(layers: Layers) {
    for (const name of Object.keys(layers) as LayerName[])
      this.groups[name].visible = layers[name];
    this.invalidate();
  }
  setCutaway(enabled: boolean) {
    this.cutaway = enabled;
    for (const { object } of this.tiles.values())
      this.applyStaticMaterial(object);
    this.invalidate();
  }
  setPickMode(mode: "start" | "goal" | null, height: number | null = null) {
    this.pickHeight = height;
    this.pickMode = height !== null && Number.isFinite(height) ? mode : null;
    this.host.style.cursor = this.pickMode ? "crosshair" : "";
  }
  setSelected(id: number | null) {
    this.selected = id;
    if (this.frame) this.drawTracks(this.frame.scene.tracks ?? []);
    this.invalidate();
  }
  preset(name: string) {
    const center = new T.Vector3(0, 0, 0.6);
    if (this.manifest?.bounds) {
      const [a, b] = normalizeBounds(this.manifest.bounds);
      center.set((a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.6);
    }
    const target = center.clone(),
      position = new T.Vector3();
    if (name === "top") position.copy(center).add(new T.Vector3(0, -0.01, 19));
    else if (name === "sensor" && this.frame?.scene.sensor_pose_world) {
      const m = this.frame.scene.sensor_pose_world;
      position.set(m[0][3], m[1][3] - 2, m[2][3] + 1.5);
      target.set(m[0][3], m[1][3] + 3, m[2][3]);
    } else position.copy(center).add(new T.Vector3(11, -13, 12));
    this.flyTo(position, target);
  }
  focusTrack(id: number) {
    const track = this.frame?.scene.tracks.find((t) => t.track_id === id);
    if (!track) return;
    const target = new T.Vector3().fromArray(track.position);
    this.flyTo(target.clone().add(new T.Vector3(4, -5, 4)), target);
  }
  /** Eased camera move; instant under reduced motion. */
  private flyTo(position: T.Vector3, target: T.Vector3, duration = 900) {
    if (this.reducedMotion.matches || duration <= 0) {
      this.flight = null;
      this.camera.position.copy(position);
      this.controls.target.copy(target);
      this.controls.update();
      this.invalidate();
      return;
    }
    this.flight = {
      fromPosition: this.camera.position.clone(),
      fromTarget: this.controls.target.clone(),
      toPosition: position.clone(),
      toTarget: target.clone(),
      start: performance.now(),
      duration,
    };
    this.invalidate();
  }
  private cancelFlight = () => {
    this.flight = null;
  };
  private stepFlight(now: number) {
    const f = this.flight;
    if (!f) return;
    const t = Math.min(1, (now - f.start) / f.duration),
      e = easeInOutCubic(t);
    this.camera.position.lerpVectors(f.fromPosition, f.toPosition, e);
    this.controls.target.lerpVectors(f.fromTarget, f.toTarget, e);
    if (t >= 1) this.flight = null;
    this.invalidate();
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
    this.failedTiles.clear();
    this.manifest = manifest;
    this.emitProgress();
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
  /**
   * One loader for all tiles. The meshopt decoder (WebAssembly, allowed by the
   * CSP's wasm-unsafe-eval) loads with the first tile, not with the studio.
   */
  private gltfLoader: Promise<GLTFLoader> | null = null;
  private loader() {
    return (this.gltfLoader ??= import(
      "three/addons/libs/meshopt_decoder.module.js"
    ).then(({ MeshoptDecoder }) =>
      new GLTFLoader().setMeshoptDecoder(MeshoptDecoder),
    ));
  }
  private emitProgress() {
    const detail: MapProgress = {
      loaded: this.tiles.size,
      total: this.manifest?.tiles.length ?? 0,
      failed: this.failedTiles.size,
    };
    this.host.dispatchEvent(new CustomEvent("tileprogress", { detail }));
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
      void this.loader()
        .then((loader) => loader.loadAsync(lod.url))
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
          this.failedTiles.delete(tile.id);
          this.emitProgress();
          this.invalidate();
        })
        .catch((error) => {
          if (generation !== this.loadGeneration) return;
          this.failedTiles.add(tile.id);
          this.emitProgress();
          this.host.dispatchEvent(
            new CustomEvent("asseterror", { detail: String(error) }),
          );
        })
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
    // Instances are pure translations (identity set once by InstancedMesh), so
    // only matrix elements 12-14 and the colour change; upload the used range.
    const matrices = this.voxelMesh.instanceMatrix,
      tint = this.voxelMesh.instanceColor!,
      m = matrices.array as Float32Array,
      c = tint.array as Float32Array;
    for (let i = 0, s = 0, o = 12; i < count; i++, s += 3, o += 16) {
      m[o] = centers[s];
      m[o + 1] = centers[s + 1];
      m[o + 2] = centers[s + 2];
      c[s] = colors?.[s] ?? 0.2;
      c[s + 1] = colors?.[s + 1] ?? 0.7;
      c[s + 2] = colors?.[s + 2] ?? 0.9;
    }
    this.voxelMesh.count = count;
    for (const [attribute, size] of [
      [matrices, 16],
      [tint, 3],
    ] as const) {
      attribute.clearUpdateRanges();
      attribute.addUpdateRange(0, count * size);
      attribute.needsUpdate = count > 0;
    }
    this.drawFlow(centers, a["voxels.flow"]);
    this.drawTracks(frame.scene.tracks ?? []);
    this.drawZones(frame.scene.geofences ?? []);
    this.drawPath(a.path);
    this.drawRisk(a);
    this.drawTraffic(frame);
    this.drawRGBD(a.gemini_inlay);
    const pose = frame.scene.sensor_pose_world;
    if (pose) this.decorative.origin.value.set(pose[0][3], pose[1][3], pose[2][3]);
    if (!this.introduced && count) {
      // One-time intro: dolly in from further out on the first populated frame.
      this.introduced = true;
      const end = this.camera.position.clone(),
        target = this.controls.target.clone();
      this.camera.position.sub(target).multiplyScalar(1.8).add(target);
      this.flyTo(end, target, 1600);
    }
    this.updateMs = performance.now() - began;
    this.invalidate();
  }
  /** Create a persistent overlay object; it is hidden while it has nothing to draw. */
  private overlay<O extends T.Object3D>(
    group: T.Group,
    sizes: Record<string, number>,
    capacity: number,
    make: (geometry: T.BufferGeometry) => O,
  ) {
    const buffer = new GrowableGeometry(sizes, capacity);
    const object = make(buffer.geometry);
    object.frustumCulled = false;
    object.visible = false;
    group.add(object);
    return { buffer, object };
  }
  private commit(
    target: { buffer: GrowableGeometry; object: T.Object3D },
    count: number,
  ) {
    target.buffer.commit(count);
    target.object.visible = count > 0;
  }
  private writeColor(array: Float32Array, offset: number, color: T.Color) {
    array[offset] = color.r;
    array[offset + 1] = color.g;
    array[offset + 2] = color.b;
  }
  private colorOf(hex: string) {
    let color = this.colors.get(hex);
    if (!color) this.colors.set(hex, (color = new T.Color(hex)));
    return color;
  }
  private drawFlow(centers: Numeric | undefined, flow: Numeric | undefined) {
    const target = this.flowLines;
    if (!centers || !flow || centers.length !== flow.length)
      return this.commit(target, 0);
    const step = Math.max(1, Math.ceil(centers.length / 3 / 800));
    target.buffer.reserve(2 * Math.ceil(centers.length / 3 / step));
    const p = target.buffer.array("position");
    let n = 0;
    for (let i = 0; i < centers.length; i += 3 * step) {
      if (Math.hypot(flow[i], flow[i + 1], flow[i + 2]) < 0.015) continue;
      for (let j = 0; j < 3; j++) {
        p[n * 3 + j] = centers[i + j];
        p[n * 3 + 3 + j] = centers[i + j] + flow[i + j];
      }
      n += 2;
    }
    this.commit(target, n);
  }
  private drawTracks(tracks: Track[]) {
    const shown = tracks.slice(0, MAX_TRACKS);
    const measured = this.trackEdges,
      predicted = this.predictedEdges,
      paths = this.trackPaths;
    for (const target of [measured, predicted])
      target.buffer.reserve(shown.length * edgeIndices.length * 2);
    paths.buffer.reserve(
      shown.reduce(
        (sum, t) => sum + 2 * Math.max(0, (t.predicted_trajectory?.length ?? 0) - 1),
        0,
      ),
    );
    const counts = { measured: 0, predicted: 0, paths: 0 };
    const mp = measured.buffer.array("position"),
      mc = measured.buffer.array("color"),
      pp = predicted.buffer.array("position"),
      pc = predicted.buffer.array("color"),
      tp = paths.buffer.array("position"),
      tc = paths.buffer.array("color");
    let picks = 0;
    this.pickIds.length = 0;
    for (const track of shown) {
      const color = this.colorOf(
        track.breached_geofences?.length
          ? "#ff6374"
          : (palette[track.semantic_class] ?? "#a7cae5"),
      );
      const selected = track.track_id === this.selected;
      const corners = track.obb_corners;
      if (corners?.length === 8) {
        const isPredicted = track.state_kind === "predicted";
        const [p, c, key] = isPredicted
          ? [pp, pc, "predicted" as const]
          : [mp, mc, "measured" as const];
        const edge = selected ? this.colorOf("#f4f8ff") : color;
        for (const [i, j] of edgeIndices)
          for (const corner of [corners[i], corners[j]]) {
            const o = counts[key] * 3;
            p[o] = corner[0];
            p[o + 1] = corner[1];
            p[o + 2] = corner[2];
            this.writeColor(c, o, edge);
            counts[key]++;
          }
      }
      this.box.makeEmpty();
      for (const corner of corners ?? [])
        this.box.expandByPoint(this.vector.fromArray(corner));
      if (this.box.isEmpty()) continue;
      // Selection and markers share one instanced draw each; scale encodes size.
      this.box.getSize(this.vector);
      this.instance.compose(
        this.box.getCenter(this.center),
        this.identity,
        this.scale.set(
          Math.max(this.vector.x, 0.15),
          Math.max(this.vector.y, 0.15),
          Math.max(this.vector.z, 0.15),
        ),
      );
      this.pickMesh.setMatrixAt(picks, this.instance);
      this.pickMesh.setColorAt(picks, color);
      const radius = selected ? 0.09 / 0.06 : 1;
      this.instance.compose(
        this.center.fromArray(track.position),
        this.identity,
        this.scale.setScalar(radius),
      );
      this.markers.setMatrixAt(picks, this.instance);
      this.markers.setColorAt(picks, color);
      this.instance.compose(
        this.center.set(track.position[0], track.position[1], 0.012),
        this.identity,
        this.scale.setScalar(1),
      );
      this.rings.setMatrixAt(picks, this.instance);
      this.rings.setColorAt(picks, color);
      this.pickIds[picks++] = track.track_id;
      const trajectory = track.predicted_trajectory ?? [];
      for (let k = 0; k + 1 < trajectory.length; k++)
        for (const point of [trajectory[k], trajectory[k + 1]]) {
          const o = counts.paths * 3;
          tp[o] = point[0];
          tp[o + 1] = point[1];
          tp[o + 2] = point[2];
          this.writeColor(tc, o, color);
          counts.paths++;
        }
    }
    this.commit(measured, counts.measured);
    this.commit(predicted, counts.predicted);
    predicted.buffer.lineDistances(counts.predicted, true);
    this.commit(paths, counts.paths);
    for (const mesh of [this.pickMesh, this.markers, this.rings]) {
      mesh.count = picks;
      mesh.visible = picks > 0 && (mesh !== this.rings || this.motion());
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
      mesh.boundingSphere = null;
    }
  }
  private drawZones(zones: Zone[]) {
    // Geofences rarely change: rebuild their meshes only when the definition does.
    const shown = zones.slice(0, 64);
    const key = JSON.stringify(
      shown.map((z) => [
        z.center,
        z.half_extents,
        z.rotation_matrix ?? zoneYaw(z),
        Boolean(z.is_breached),
      ]),
    );
    if (key === this.zoneKey) return;
    this.zoneKey = key;
    const group = this.groups.geofences;
    clearGroup(group);
    this.breachedFills = [];
    for (const zone of shown) {
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
      if (zone.is_breached) this.breachedFills.push(fill.material);
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
    const target = this.pathLine;
    if (!path || path.length < 6) return this.commit(target, 0);
    const n = Math.floor(path.length / 3);
    target.buffer.reserve(n);
    target.buffer.array("position").set(
      path.length === n * 3 ? path : path.subarray(0, n * 3),
    );
    this.commit(target, n);
    target.buffer.lineDistances(n, false);
  }
  private drawRisk(arrays: Record<string, Numeric>) {
    const target = this.riskPoints;
    const centers = arrays["floor_risk.centers"],
      risk = arrays["floor_risk.risk"],
      colors = arrays["floor_risk.colors"];
    if (!centers || !risk) return this.commit(target, 0);
    const limit = Math.min(risk.length, 20000);
    target.buffer.reserve(limit);
    const p = target.buffer.array("position"),
      c = target.buffer.array("color");
    let n = 0;
    for (let i = 0; i < limit; i++)
      if (risk[i] > 0.15) {
        const o = n * 3,
          s = i * 3;
        p[o] = centers[s];
        p[o + 1] = centers[s + 1];
        p[o + 2] = centers[s + 2] + 0.015;
        c[o] = colors?.[s] ?? risk[i];
        c[o + 1] = colors?.[s + 1] ?? 0.35;
        c[o + 2] = colors?.[s + 2] ?? 0.1;
        n++;
      }
    this.commit(target, n);
  }
  private drawTraffic(frame: SceneFrame) {
    const target = this.trafficPoints;
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
      return this.commit(target, 0);
    let peak = 0;
    for (let i = 0; i < values.length; i++) peak = Math.max(peak, values[i]);
    if (!peak) return this.commit(target, 0);
    target.buffer.reserve(values.length);
    const p = target.buffer.array("position"),
      c = target.buffer.array("color");
    let n = 0;
    for (let i = 0; i < values.length; i++) {
      if (values[i] < peak * 0.025) continue;
      const y = Math.floor(i / shape[1]),
        x = i % shape[1],
        o = n * 3,
        t = values[i] / peak;
      p[o] = bounds[0] + ((x + 0.5) / shape[1]) * (bounds[1] - bounds[0]);
      p[o + 1] = bounds[2] + ((y + 0.5) / shape[0]) * (bounds[3] - bounds[2]);
      p[o + 2] = 0.045;
      c[o] = 0.4 + 0.6 * t;
      c[o + 1] = 0.15 + 0.4 * t;
      c[o + 2] = 0.55 - 0.4 * t;
      n++;
    }
    this.commit(target, n);
  }
  private drawRGBD(values: Numeric | undefined) {
    const target = this.rgbdPoints;
    if (!values) return this.commit(target, 0);
    const count = Math.min(Math.floor(values.length / 6), 20000);
    target.buffer.reserve(count);
    const p = target.buffer.array("position"),
      c = target.buffer.array("color");
    for (let i = 0; i < count; i++)
      for (let j = 0; j < 3; j++) {
        p[i * 3 + j] = values[i * 6 + j];
        const v = values[i * 6 + j + 3];
        c[i * 3 + j] = v > 1 ? v / 255 : v;
      }
    this.commit(target, count);
  }
  /** Request a render on the next animation frame (used by on-demand quality). */
  invalidate = () => {
    this.dirty = true;
  };
  private markDirty = () => {
    this.dirty = true;
    this.skipInterval = true;
  };
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
    const hit = this.ray.intersectObject(this.pickMesh, false)[0];
    if (hit?.instanceId !== undefined) this.onPick(this.pickIds[hit.instanceId]);
  };
  private animate = () => {
    if (this.disposed || this.frozen) return;
    this.raf = requestAnimationFrame(this.animate);
    const now = performance.now();
    this.stepFlight(now);
    const moved = this.controls.update();
    const continuous = this.resolved !== "performance";
    // Offscreen canvases and settled on-demand scenes skip rendering entirely.
    if (!this.onscreen || (!continuous && !moved && !this.dirty)) {
      this.skipInterval = true;
      this.last = now;
      this.tick(now);
      return;
    }
    this.dirty = false;
    // The gap after a hidden tab, an offscreen period or an idle on-demand
    // stretch is not a frame interval.
    if (continuous && !this.skipInterval) this.intervals.push(now - this.last);
    this.skipInterval = false;
    if (this.intervals.length > 1200) this.intervals.shift();
    this.decorate(now);
    this.renderer.info.reset();
    const renderStart = performance.now();
    if (this.resolved === "cinematic" && this.effects) this.effects.render();
    else this.renderer.render(this.scene, this.camera);
    this.renderSubmitMs = performance.now() - renderStart;
    this.frames++;
    this.last = now;
    this.tick(now);
  };
  /** Once-per-second work: LOD refresh, metrics and adaptive pixel ratio. */
  private tick(now: number) {
    if (now - this.lodClock > 1000) {
      this.lodClock = now;
      this.refreshLods();
    }
    if (now - this.measured <= 1000) return;
    const ordered = [...this.intervals].sort((a, b) => a - b);
    const percentile = (p: number) =>
      ordered[Math.min(ordered.length - 1, Math.floor(ordered.length * p))] ??
      0;
    const buffer = this.renderer.getDrawingBufferSize(new T.Vector2());
    const metrics: RenderMetrics = {
      fps: (this.frames * 1000) / (now - this.measured),
      frameMs: this.frames ? (now - this.measured) / this.frames : 0,
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
      quality: this.resolved,
      qualitySetting: this.quality,
      pixelRatio: this.renderer.getPixelRatio(),
      renderMode: this.resolved === "performance" ? "on-demand" : "continuous",
      effects: {
        postprocessing: this.resolved === "cinematic" && this.effects !== null,
        motion: this.motion(),
      },
    };
    this.adaptPixelRatio(metrics);
    this.adaptTier(metrics);
    // Stable hooks for end-to-end assertions and quick inspection.
    Object.assign(this.host.dataset, {
      instances: String(metrics.instances),
      tiles: String(metrics.tiles),
      quality: metrics.quality,
      qualitySetting: metrics.qualitySetting,
      dpr: metrics.pixelRatio.toFixed(2),
      renderMode: metrics.renderMode,
      effects: metrics.effects.postprocessing ? "cinematic" : "off",
      motion: String(metrics.effects.motion),
    });
    this.metricsCallback(metrics);
    this.frames = 0;
    this.measured = now;
  }
  setQuality(quality: Quality) {
    this.quality = quality;
    this.resolved = quality === "auto" ? "balanced" : quality;
    this.slowWindows = this.fastWindows = 0;
    this.intervals.length = 0;
    this.applyPixelRatio(this.pixelRatioCap());
    this.applyTier();
    this.markDirty();
  }
  /** Motion effects run only in the cinematic tier and never under reduced motion. */
  private motion() {
    return this.resolved === "cinematic" && !this.reducedMotion.matches;
  }
  private applyTier() {
    const cinematic = this.resolved === "cinematic";
    this.scene.fog = cinematic ? this.fog : null;
    if (!cinematic || this.effects || this.effectsLoading) return;
    this.effectsLoading = true;
    void import("./effects")
      .then(({ CinematicEffects }) => {
        if (this.disposed) return;
        this.effects = new CinematicEffects(this.renderer, this.scene, this.camera);
        this.resizeViewport();
      })
      .catch((error) =>
        this.host.dispatchEvent(new CustomEvent("asseterror", { detail: String(error) })),
      )
      .finally(() => (this.effectsLoading = false));
  }
  private decorate(now: number) {
    const motion = this.motion(),
      seconds = (now - this.epoch) / 1000;
    this.decorative.time.value = seconds;
    this.decorative.strength.value = motion ? 1 : 0;
    this.dashOffset.value = motion ? seconds * 0.35 : 0;
    this.rings.visible = motion && this.rings.count > 0;
    const glow = motion ? 0.07 * (0.5 + 0.5 * Math.sin(seconds * 4)) : 0;
    for (const material of this.breachedFills) material.opacity = 0.055 + glow;
  }
  private pixelRatioCap() {
    return this.options.pixelRatio ?? PIXEL_RATIO_CAP[this.resolved]();
  }
  private applyPixelRatio(ratio: number) {
    if (Math.abs(ratio - this.renderer.getPixelRatio()) < 0.01) return;
    this.renderer.setPixelRatio(ratio);
    this.resizeViewport();
  }
  /**
   * `auto` moves to cinematic after 10 s with frame p95 at or under 16.7 ms
   * (60 Hz budget) at full pixel ratio, and back to balanced after 3 s over
   * 25 ms. Thresholds are a starting point, see docs/REVIEW_AND_ROADMAP.md.
   */
  private adaptTier(metrics: RenderMetrics) {
    if (this.quality !== "auto" || !this.frames) return;
    const atCap = metrics.pixelRatio >= this.pixelRatioCap() - 0.01;
    if (this.resolved === "balanced") {
      this.headroomWindows =
        atCap && metrics.frameMsP95 > 0 && metrics.frameMsP95 <= 16.7
          ? this.headroomWindows + 1
          : 0;
      // A demotion is final for the session, so auto never oscillates.
      if (this.headroomWindows >= 10 && !this.autoDemoted) this.switchAutoTier("cinematic");
    } else if (this.resolved === "cinematic") {
      this.overBudgetWindows = metrics.frameMsP95 > 25 ? this.overBudgetWindows + 1 : 0;
      if (this.overBudgetWindows >= 3) this.switchAutoTier("balanced");
    }
  }
  private switchAutoTier(tier: ResolvedQuality) {
    this.resolved = tier;
    if (tier === "balanced") this.autoDemoted = true;
    this.headroomWindows = this.overBudgetWindows = 0;
    this.intervals.length = 0;
    this.applyTier();
    this.markDirty();
  }
  /**
   * Frame-rate driven resolution scaling with hysteresis: continuous tiers
   * only, never when the caller fixed the pixel ratio (benchmarks do). Below
   * 45 fps for 2 s steps down; 55 fps or more for 5 s steps back up.
   */
  private adaptPixelRatio(metrics: RenderMetrics) {
    if (
      this.options.pixelRatio !== undefined ||
      metrics.renderMode !== "continuous" ||
      !this.frames
    )
      return;
    const ratio = this.renderer.getPixelRatio(),
      cap = this.pixelRatioCap();
    this.slowWindows = metrics.fps < 45 ? this.slowWindows + 1 : 0;
    this.fastWindows = metrics.fps >= 55 ? this.fastWindows + 1 : 0;
    if (this.slowWindows >= 2 && ratio > MIN_PIXEL_RATIO) {
      this.applyPixelRatio(Math.max(MIN_PIXEL_RATIO, ratio - 0.15));
      this.slowWindows = 0;
    } else if (this.fastWindows >= 5 && ratio < cap) {
      this.applyPixelRatio(Math.min(cap, ratio + 0.15));
      this.fastWindows = 0;
    }
  }
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
    this.visibility.disconnect();
    this.controls.removeEventListener("change", this.invalidate);
    this.controls.removeEventListener("start", this.cancelFlight);
    this.reducedMotion.removeEventListener("change", this.invalidate);
    this.effects?.dispose();
    this.effects = null;
    document.removeEventListener("visibilitychange", this.markDirty);
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
