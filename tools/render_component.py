"""Render generated geometry to a PNG; never captures or controls desktop apps.

Windows Open3D wheels lack EGL headless support. A newly-created hidden surface
supplies the render context, and Application.render_to_image reads only our
generated scene. This is separate from interactive computer-use validation.
"""
from __future__ import annotations
import argparse
import ctypes
import gc
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend",choices=["native","mesa"],default="native")
    parser.add_argument("--mesa-dir",type=Path,default=ROOT/"assets/rendering/mesa")
    parser.add_argument("--output",type=Path,default=ROOT/"artifacts/render_component.png")
    args=parser.parse_args()
    # Match main.py's import order to detect accidental early OpenGL loading.
    import numpy as np
    import torch
    import yaml
    from visualization.render_backend import configure_rendering
    backend=configure_rendering(args.backend,args.mesa_dir if args.backend=="mesa" else None)
    print(backend["name"],flush=True)
    import open3d as o3d
    from open3d.visualization import gui,rendering
    app=gui.Application.instance; app.initialize()
    window=app.create_window("Generated rendering component",320,240,flags=1)
    window.show(False)
    scene=rendering.Open3DScene(window.renderer)
    try:
        scene.set_background([.025,.04,.06,1])
        sphere=o3d.geometry.TriangleMesh.create_sphere(radius=.7,resolution=32)
        sphere.compute_vertex_normals()
        material=rendering.MaterialRecord(); material.shader="defaultLit"
        material.base_color=[.95,.18,.08,1]; material.base_roughness=.35; material.base_metallic=.15
        scene.add_geometry("sphere",sphere,material)
        scene.camera.look_at([0,0,0],[2.5,-2.5,1.6],[0,0,1])
        scene.camera.set_projection(45,320/240,.1,20,rendering.Camera.FovType.Vertical)
        image=app.render_to_image(scene,320,240); pixels=np.asarray(image)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        if not o3d.io.write_image(str(args.output),image): raise OSError(args.output)
        foreground=(pixels[:,:,0]>pixels[:,:,1]*1.4)&(pixels[:,:,0]>pixels[:,:,2]*1.4)&(pixels[:,:,0]>80)
        report={"open3d":o3d.__version__,"backend":backend["name"],"shape":list(pixels.shape),
            "pixel_std":float(pixels.std()),"foreground_pixels":int(foreground.sum()),
            "unique_colors":int(len(np.unique(pixels.reshape(-1,pixels.shape[-1]),axis=0))),
            "test":"generated_geometry_component_not_desktop_capture"}
        if args.backend=="mesa":
            adapter=ctypes.WinDLL(str(args.mesa_dir.resolve()/"opengl32.dll"))
            report["wgl_attribute_fixes"]=adapter.LabWglCompatibilityFixCount()
            report["wgl_framebuffer_filters"]=adapter.LabWglFramebufferFilterCount()
        report["passed"]=report["foreground_pixels"]>1000 and report["unique_colors"]>100
        args.output.with_suffix(".json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        print(json.dumps(report),flush=True)
    finally:
        scene=None; gc.collect(); window.close(); window=None
        app.run_one_tick(); app.quit(); app.run_one_tick()
    print("Normal component teardown completed",flush=True)
    return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())
