import * as T from "three";
import { EffectComposer } from "three/addons/postprocessing/EffectComposer.js";
import { OutputPass } from "three/addons/postprocessing/OutputPass.js";
import { RenderPass } from "three/addons/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/addons/postprocessing/UnrealBloomPass.js";

/**
 * Cinematic post-processing, loaded only when the cinematic tier is active.
 * Presentation only: bloom changes how bright marks look, never what the data says.
 */
export class CinematicEffects {
  private composer: EffectComposer;
  constructor(
    renderer: T.WebGLRenderer,
    scene: T.Scene,
    camera: T.Camera,
  ) {
    const size = renderer.getDrawingBufferSize(new T.Vector2());
    // Half-float with MSAA keeps HDR values for bloom and the antialiasing
    // the default framebuffer would have had.
    const target = new T.WebGLRenderTarget(size.x, size.y, {
      type: T.HalfFloatType,
      samples: 4,
    });
    this.composer = new EffectComposer(renderer, target);
    this.composer.addPass(new RenderPass(scene, camera));
    this.composer.addPass(new UnrealBloomPass(size, 0.55, 0.4, 0.82));
    // Tone mapping and sRGB conversion from the renderer's own settings.
    this.composer.addPass(new OutputPass());
  }
  setSize(width: number, height: number, pixelRatio: number) {
    this.composer.setPixelRatio(pixelRatio);
    this.composer.setSize(width, height);
  }
  render() {
    this.composer.render();
  }
  dispose() {
    for (const pass of this.composer.passes) pass.dispose();
    this.composer.dispose();
  }
}
