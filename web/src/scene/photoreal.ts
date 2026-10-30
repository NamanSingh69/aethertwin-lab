import * as T from "three";
import { RoomEnvironment } from "three/addons/environments/RoomEnvironment.js";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { GTAOPass } from "three/addons/postprocessing/GTAOPass.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

export type Bounds = [[number, number, number], [number, number, number]];

/** Concrete-like floor albedo and roughness from value noise; no external image. */
function floorTextures(size = 512) {
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  const image = ctx?.createImageData(size, size);
  let seed = 11;
  const random = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);
  const lattice = Array.from({ length: 33 * 33 }, random);
  const smooth = (t: number) => t * t * (3 - 2 * t);
  const noise = (x: number, y: number, cells: number) => {
    const fx = (x / size) * cells, fy = (y / size) * cells;
    const ix = Math.floor(fx), iy = Math.floor(fy), tx = smooth(fx - ix), ty = smooth(fy - iy);
    const at = (i: number, j: number) => lattice[((j % cells) * 33 + (i % cells)) % lattice.length];
    const top = at(ix, iy) * (1 - tx) + at(ix + 1, iy) * tx;
    const bottom = at(ix, iy + 1) * (1 - tx) + at(ix + 1, iy + 1) * tx;
    return top * (1 - ty) + bottom * ty;
  };
  if (ctx && image) {
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) {
        const value = 0.5 * noise(x, y, 8) + 0.3 * noise(x, y, 32) + 0.2 * random();
        const shade = 92 + 18 * value, i = 4 * (y * size + x);
        image.data[i] = shade;
        image.data[i + 1] = shade + 2;
        image.data[i + 2] = shade + 5;
        image.data[i + 3] = 255;
      }
    ctx.putImageData(image, 0, 0);
  }
  const albedo = new T.CanvasTexture(canvas);
  albedo.colorSpace = T.SRGBColorSpace;
  albedo.wrapS = albedo.wrapT = T.RepeatWrapping;
  albedo.anisotropy = 4;
  return albedo;
}

/**
 * Photoreal presentation: image-based lighting from a procedural room, soft
 * shadows, ambient occlusion and physical materials. Loaded only for the
 * photoreal tier. It changes how surfaces look, never their geometry or the
 * data drawn on top, and it is not a capture of the real lab.
 */
export class PhotorealLook {
  private composer: EffectComposer;
  private gtao: GTAOPass;
  private environment: T.Texture;
  private pmrem: T.PMREMGenerator;
  private key = new T.DirectionalLight("#fff4e6", 1.6);
  private floor: T.Mesh<T.PlaneGeometry, T.MeshStandardMaterial>;
  private prepared = new WeakSet<T.Material>();
  private active = false;
  private saved: { toneMapping: T.ToneMapping; exposure: number; clear: T.Color } | null = null;

  constructor(
    private renderer: T.WebGLRenderer,
    private scene: T.Scene,
    camera: T.Camera,
  ) {
    this.pmrem = new T.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    this.environment = this.pmrem.fromScene(room, 0.04).texture;
    room.dispose();
    const size = renderer.getDrawingBufferSize(new T.Vector2());
    const target = new T.WebGLRenderTarget(size.x, size.y, { type: T.HalfFloatType, samples: 4 });
    this.composer = new EffectComposer(renderer, target);
    this.composer.addPass(new RenderPass(scene, camera));
    this.gtao = new GTAOPass(scene, camera, size.x, size.y);
    this.gtao.blendIntensity = 0.85;
    this.composer.addPass(this.gtao);
    // A faint bloom only on genuinely bright marks (status lights, selections).
    this.composer.addPass(new UnrealBloomPass(size, 0.18, 0.35, 0.95));
    this.composer.addPass(new OutputPass());

    const albedo = floorTextures();
    this.floor = new T.Mesh(
      new T.PlaneGeometry(1, 1),
      new T.MeshStandardMaterial({ map: albedo, roughness: 0.78, metalness: 0, envMapIntensity: 0.6 }),
    );
    this.floor.name = "photoreal-floor";
    this.floor.receiveShadow = true;
    this.floor.position.z = -0.03;
    this.key.castShadow = true;
    this.key.shadow.mapSize.set(2048, 2048);
    this.key.shadow.bias = -0.0004;
    this.key.shadow.normalBias = 0.02;
    this.key.shadow.radius = 4;
  }

  /** Fit the floor, key light and shadow frustum to the map bounds. */
  fit([min, max]: Bounds) {
    const cx = (min[0] + max[0]) / 2, cy = (min[1] + max[1]) / 2;
    const span = Math.max(max[0] - min[0], max[1] - min[1], 4) + 6;
    this.floor.scale.set(span, span, 1);
    this.floor.position.set(cx, cy, min[2] - 0.03);
    const texture = this.floor.material.map;
    if (texture) texture.repeat.set(span / 4, span / 4);
    this.key.position.set(cx + span * 0.35, cy - span * 0.45, max[2] + span * 0.6);
    this.key.target.position.set(cx, cy, min[2]);
    const shadow = this.key.shadow.camera;
    shadow.left = shadow.bottom = -span / 2;
    shadow.right = shadow.top = span / 2;
    shadow.near = 0.5;
    shadow.far = span * 3;
    shadow.updateProjectionMatrix();
  }

  /** Physical surface settings for loaded map meshes; data overlays are left alone. */
  prepare(object: T.Object3D) {
    object.traverse((node) => {
      if (!(node instanceof T.Mesh)) return;
      node.castShadow = node.receiveShadow = true;
      if (!node.geometry.getAttribute("normal")) node.geometry.computeVertexNormals();
      for (const material of Array.isArray(node.material) ? node.material : [node.material]) {
        if (!(material instanceof T.MeshStandardMaterial) || this.prepared.has(material)) continue;
        this.prepared.add(material);
        material.userData.flat = { roughness: material.roughness, metalness: material.metalness };
        material.envMapIntensity = 0.9;
      }
    });
    this.applyMaterials(object);
  }

  private applyMaterials(object: T.Object3D) {
    object.traverse((node) => {
      if (!(node instanceof T.Mesh)) return;
      for (const material of Array.isArray(node.material) ? node.material : [node.material])
        if (material instanceof T.MeshStandardMaterial && material.userData.flat) {
          // Painted plaster and fabric read as rough dielectrics under image-based light.
          material.roughness = this.active ? 0.72 : material.userData.flat.roughness;
          material.metalness = this.active ? 0 : material.userData.flat.metalness;
        }
    });
  }

  setActive(active: boolean, root: T.Object3D) {
    if (active === this.active) return;
    this.active = active;
    if (active) {
      this.saved = {
        toneMapping: this.renderer.toneMapping,
        exposure: this.renderer.toneMappingExposure,
        clear: this.renderer.getClearColor(new T.Color()),
      };
      this.renderer.toneMapping = T.AgXToneMapping;
      this.renderer.toneMappingExposure = 0.95;
      // The studio's stylised lights would double the image-based light.
      this.scene.traverse((node) => {
        if (!(node instanceof T.Light) || node === this.key) return;
        node.userData.studioIntensity ??= node.intensity;
        node.intensity = node.userData.studioIntensity * (node instanceof T.HemisphereLight ? 0.15 : 0);
      });
      this.renderer.setClearColor("#1b2129");
      this.renderer.shadowMap.enabled = true;
      this.renderer.shadowMap.type = T.PCFSoftShadowMap;
      this.scene.environment = this.environment;
      this.scene.environmentIntensity = 0.55;
      this.scene.add(this.floor, this.key, this.key.target);
    } else {
      if (this.saved) {
        this.renderer.toneMapping = this.saved.toneMapping;
        this.renderer.toneMappingExposure = this.saved.exposure;
        this.renderer.setClearColor(this.saved.clear);
      }
      this.renderer.shadowMap.enabled = false;
      this.scene.environment = null;
      this.scene.traverse((node) => {
        if (node instanceof T.Light && node.userData.studioIntensity !== undefined)
          node.intensity = node.userData.studioIntensity;
      });
      this.scene.remove(this.floor, this.key, this.key.target);
    }
    // Shader programs depend on shadow and environment state.
    this.scene.traverse((node) => {
      if (node instanceof T.Mesh)
        for (const material of Array.isArray(node.material) ? node.material : [node.material])
          material.needsUpdate = true;
    });
    this.applyMaterials(root);
  }

  setSize(width: number, height: number, pixelRatio: number) {
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(width, height);
  }

  render() {
    this.composer.render();
  }

  dispose() {
    this.scene.remove(this.floor, this.key, this.key.target);
    this.floor.geometry.dispose();
    this.floor.material.map?.dispose();
    this.floor.material.dispose();
    this.key.dispose();
    this.environment.dispose();
    this.pmrem.dispose();
    for (const pass of this.composer.passes) pass.dispose();
    this.composer.dispose();
  }
}
