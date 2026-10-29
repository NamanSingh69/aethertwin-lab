import { useEffect, useRef, useState, type RefObject } from "react";
import type { SpatialRenderer, RenderMetrics } from "../scene/SpatialRenderer";
import type { Layers, SceneFrame, Vec3 } from "../lib/types";
import type { Notice } from "./useStudioSession";

export interface RendererSettings {
  layers: Layers;
  selected: number | null;
  cutaway: boolean;
  pickMode: "start" | "goal" | null;
  activePickHeight: number | null;
  mapManifestUrl: string | null | undefined;
  mapRevision: string | undefined;
}

/**
 * Lazily imports Three.js, mounts one renderer into `host`, and keeps it in
 * sync with presentation settings. Frames arriving before the renderer exists
 * are replayed from `latest` once it starts.
 */
export function useSpatialRenderer(
  host: RefObject<HTMLDivElement | null>,
  latest: RefObject<SceneFrame | null>,
  settings: RendererSettings,
  handlers: {
    onSelect: (id: number) => void;
    onWorldPick: (point: Vec3, target: "start" | "goal") => void;
    setNotice: (notice: Notice) => void;
    setError: (message: string) => void;
  },
) {
  const renderer = useRef<SpatialRenderer | null>(null);
  const [metrics, setMetrics] = useState<RenderMetrics | null>(null);
  const settingsRef = useRef(settings),
    handlersRef = useRef(handlers);
  settingsRef.current = settings;
  handlersRef.current = handlers;
  const loadMap = (instance: SpatialRenderer, url: string) =>
    void instance.loadManifest(url).catch((cause) =>
      handlersRef.current.setNotice({
        text: `Measured map: ${String(cause)}`,
        bad: true,
      }),
    );
  useEffect(() => {
    if (!host.current) return;
    let disposed = false;
    const element = host.current;
    const failed = (event: Event) =>
      handlersRef.current.setNotice({
        text: `Map layer unavailable: ${(event as CustomEvent).detail}`,
        bad: true,
      });
    element.addEventListener("asseterror", failed);
    void import("../scene/SpatialRenderer")
      .then(({ SpatialRenderer }) => {
        if (disposed) return;
        const instance = new SpatialRenderer(
          element,
          (id) => handlersRef.current.onSelect(id),
          (point, target) => handlersRef.current.onWorldPick(point, target),
          setMetrics,
        );
        renderer.current = instance;
        const s = settingsRef.current;
        instance.setLayers(s.layers);
        instance.setCutaway(s.cutaway);
        instance.setSelected(s.selected);
        instance.setPickMode(s.pickMode, s.activePickHeight);
        if (latest.current) instance.updateFrame(latest.current);
        if (s.mapManifestUrl) loadMap(instance, s.mapManifestUrl);
      })
      .catch((cause) => {
        if (!disposed)
          handlersRef.current.setError(
            `3D rendering could not start: ${String(cause)}`,
          );
      });
    return () => {
      disposed = true;
      element.removeEventListener("asseterror", failed);
      renderer.current?.dispose();
      renderer.current = null;
    };
  }, [host, latest]);
  useEffect(() => {
    if (renderer.current && settings.mapManifestUrl)
      loadMap(renderer.current, settings.mapManifestUrl);
  }, [settings.mapRevision, settings.mapManifestUrl]);
  useEffect(() => {
    renderer.current?.setLayers(settings.layers);
  }, [settings.layers]);
  useEffect(() => {
    renderer.current?.setCutaway(settings.cutaway);
  }, [settings.cutaway]);
  useEffect(() => {
    renderer.current?.setSelected(settings.selected);
  }, [settings.selected]);
  useEffect(() => {
    renderer.current?.setPickMode(settings.pickMode, settings.activePickHeight);
  }, [settings.pickMode, settings.activePickHeight]);
  return { renderer, metrics };
}
