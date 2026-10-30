import { initialLayers, type LayerName, type Layers } from "./types";

export const VIEW_PRESETS = ["isometric", "top", "sensor"] as const;
export type ViewPreset = (typeof VIEW_PRESETS)[number];

/** Studio URL that opens with only `layers` visible and the camera at `view`. */
export function studioLink(layers: LayerName[], view: ViewPreset) {
  return `./?${new URLSearchParams({ layers: layers.join(","), view })}`;
}

/** Reads `?layers=a,b&view=top`; unknown names are ignored, absent params keep the defaults. */
export function parseViewParams(search: string): { layers: Layers; view: ViewPreset | null } {
  const params = new URLSearchParams(search);
  const requested = params.get("layers");
  let layers = initialLayers;
  if (requested !== null) {
    const names = new Set(requested.split(","));
    const known = (Object.keys(initialLayers) as LayerName[]).filter((name) => names.has(name));
    if (known.length)
      layers = Object.fromEntries(
        (Object.keys(initialLayers) as LayerName[]).map((name) => [name, known.includes(name)]),
      ) as Layers;
  }
  const view = params.get("view");
  return { layers, view: VIEW_PRESETS.find((preset) => preset === view) ?? null };
}
