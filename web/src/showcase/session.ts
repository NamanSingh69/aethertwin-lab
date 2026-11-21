import type { Connection } from "../lib/api";
import type { Bootstrap, HaloSnapshot, SceneFrame } from "../lib/types";
import { parseShowcaseIndex, resolveShowcaseBase, type ShowcaseIndex } from "./format";
import {
  ShowcaseStream,
  type DecoderPort,
  type PlaybackState,
  type PlayerClock,
} from "./ShowcaseStream";

export type { PlaybackState } from "./ShowcaseStream";

/** The data base for this page: `?data=` (same origin), VITE_SHOWCASE_BASE, or ./showcase/. */
export function showcaseBase() {
  return resolveShowcaseBase(
    window.location.search,
    window.location.href,
    import.meta.env.VITE_SHOWCASE_BASE as string | undefined,
  );
}

async function getJSON(url: string, fetcher: typeof fetch): Promise<unknown> {
  const response = await fetcher(url, { credentials: "same-origin" });
  if (!response.ok) throw new Error(`${new URL(url).pathname} returned ${response.status}`);
  return response.json();
}

const indexes = new Map<string, Promise<ShowcaseIndex>>();
/** index.json for `base`, fetched once per page (the banner and the session share it). */
export function loadShowcaseIndex(base: string, fetcher: typeof fetch = (i, o) => globalThis.fetch(i, o)) {
  let pending = indexes.get(base);
  if (!pending) {
    pending = getJSON(new URL("index.json", base).href, fetcher).then(parseShowcaseIndex);
    // A failed load is retried by the next caller.
    pending.catch(() => indexes.delete(base));
    indexes.set(base, pending);
  }
  return pending;
}

/**
 * A static stand-in for GET /api/v2/bootstrap. Only presentation fields are
 * taken from bootstrap.json; the session is always a viewer of a recorded
 * replay with no service, no credentials, no command capabilities and no
 * hardware claim, whatever the file says.
 */
export function showcaseBootstrap(raw: unknown, index: ShowcaseIndex, base: string): Bootstrap {
  const file = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  const revision =
    typeof file.map_revision === "string"
      ? file.map_revision
      : (index.assets_prefix.match(/assets\/([^/]+)\//)?.[1] ?? "");
  const halo = file.halo && typeof file.halo === "object" ? (file.halo as HaloSnapshot) : undefined;
  return {
    protocol_version: 2,
    identity: { name: index.source.recording || "recorded replay", role: "viewer" },
    role: "viewer",
    source_mode: "replay",
    hardware_status: "not_tested",
    map_revision: revision,
    // Tiles are resolved next to the manifest (lod `file` names), never /api/v2/assets.
    map_manifest_url: index.assets_prefix
      ? new URL(`${index.assets_prefix}manifest.json`, base).href
      : "",
    control_revision: 0,
    capabilities: {},
    latest: null,
    paused: false,
    error: null,
    source_active: true,
    recording_id: index.source.recording,
    replay: { frame_count: index.frame_count, current_frame: 0, duration: index.duration_s },
    ...(halo ? { halo: { ...halo, virtual: true, physical_verified: false } } : {}),
  };
}

export interface ShowcaseSession {
  index: ShowcaseIndex;
  bootstrap: Bootstrap;
  stream: ShowcaseStream;
}

/** Loads index.json and bootstrap.json from `base` and starts the replay. */
export async function startShowcase(options: {
  base: string;
  onBootstrap: (bootstrap: Bootstrap) => void;
  onFrame: (frame: SceneFrame) => void;
  onStatus: (status: Connection, message?: string) => void;
  onPlayback: (state: PlaybackState) => void;
  fetch?: typeof fetch;
  createDecoder?: () => DecoderPort;
  clock?: PlayerClock;
}): Promise<ShowcaseSession> {
  const fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
  const index = await loadShowcaseIndex(options.base, fetcher);
  const raw = await getJSON(new URL(index.bootstrap, options.base).href, fetcher);
  const bootstrap = showcaseBootstrap(raw, index, options.base);
  options.onBootstrap(bootstrap);
  const stream = new ShowcaseStream(options.base, index, options.onFrame, options.onStatus, {
    fetch: fetcher,
    createDecoder: options.createDecoder,
    onPlayback: options.onPlayback,
    ...options.clock,
  });
  return { index, bootstrap, stream };
}
