import type { Connection, SceneSource } from "../lib/api";
import type { SceneFrame } from "../lib/types";
import { chunkIndexForFrame, gunzip, parseRecords, type ReplayRecord, type ShowcaseIndex } from "./format";

/** The part of a Worker the player uses; tests pass an in-process decoder. */
export interface DecoderPort {
  postMessage(message: unknown, transfer?: Transferable[]): void;
  onmessage: ((event: MessageEvent) => void) | null;
  terminate(): void;
}
export interface PlaybackState {
  paused: boolean;
  /** Reached the last frame with looping off. */
  ended: boolean;
  /** Index of the last frame handed to the decoder, 0-based over the whole replay. */
  frame: number;
  frameCount: number;
  /** Replay position in seconds since the start of the recording. */
  t: number;
  duration: number;
  loop: boolean;
  buffering: boolean;
  /** Download progress of the chunk playback is waiting for, while buffering. */
  loading: { chunk: number; chunks: number; received: number; total: number } | null;
}
export interface PlayerClock {
  now: () => number;
  schedule: (fn: () => void, ms: number) => unknown;
  cancel: (handle: unknown) => void;
}
export interface ShowcaseOptions extends Partial<PlayerClock> {
  fetch?: typeof fetch;
  createDecoder?: () => DecoderPort;
  onPlayback?: (state: PlaybackState) => void;
  loop?: boolean;
}

/** Behind the recorded timeline by more than this, the clock slips instead of bursting. */
const MAX_LAG_S = 0.75;
const RETRY_MS = 3000;
const MIN_LOOP_GAP_S = 0.05;
const MAX_BODY_BYTES = 256 * 1024 * 1024;
const MAX_LOOP_GAP_S = 2;
const MAX_RETRIES = 5;

/**
 * Plays a static showcase recording through the studio's decoder worker with
 * the same callbacks as SceneStream. Chunks are fetched lazily, one ahead, and
 * every packet is decoded in order (deltas cannot be skipped). Seeking starts
 * at the keyframe that opens the chunk holding the requested frame.
 */
export class ShowcaseStream implements SceneSource {
  public dropped = 0;
  private decoder: DecoderPort;
  private cache = new Map<number, Promise<ReplayRecord[]>>();
  private progress = new Map<number, number>();
  private records: ReplayRecord[] | null = null;
  private chunk = 0;
  private pos = 0;
  private generation = 0;
  private anchorT = 0;
  private anchorWall = 0;
  private paused = false;
  private ended = false;
  private buffering = true;
  private loop: boolean;
  private cursorFrame = 0;
  private lastFrameT: number | null = null;
  private inflight: Array<{ generation: number; t: number; frame: number }> = [];
  private timer: unknown = null;
  private retries = 0;
  private disposed = false;
  private clock: PlayerClock;
  private fetcher: typeof fetch;

  constructor(
    private base: string,
    private index: ShowcaseIndex,
    private onFrame: (frame: SceneFrame) => void,
    private onStatus: (status: Connection, message?: string) => void,
    private options: ShowcaseOptions = {},
  ) {
    this.clock = {
      now: options.now ?? (() => performance.now()),
      schedule: options.schedule ?? ((fn, ms) => setTimeout(fn, ms)),
      cancel: options.cancel ?? ((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)),
    };
    this.fetcher = options.fetch ?? ((input, init) => globalThis.fetch(input, init));
    this.loop = options.loop ?? true;
    this.decoder =
      options.createDecoder?.() ??
      (new Worker(new URL("../scene/decode.worker.ts", import.meta.url), {
        type: "module",
      }) as DecoderPort);
    this.decoder.onmessage = (event) => this.decoded(event.data);
    this.anchorT = index.chunks[0].start_s;
    this.anchorWall = this.clock.now();
    this.onStatus("connecting", "Loading the recorded replay");
    this.pump();
  }

  get state(): PlaybackState {
    return {
      paused: this.paused,
      ended: this.ended,
      frame: this.cursorFrame,
      frameCount: this.index.frame_count,
      t: this.position(),
      duration: this.index.duration_s,
      loop: this.loop,
      buffering: this.buffering,
      loading: this.buffering
        ? {
            chunk: this.chunk,
            chunks: this.index.chunks.length,
            received: this.progress.get(this.chunk) ?? 0,
            total: this.index.chunks[this.chunk].bytes,
          }
        : null,
    };
  }
  /** Seconds of recorded time since the frame on screen was recorded. */
  observationAge(): number | null {
    return this.lastFrameT === null ? null : Math.max(0, this.position() - this.lastFrameT);
  }
  play() {
    if (this.disposed || !this.paused) return;
    this.paused = false;
    if (this.ended) {
      this.restartAt(0);
      return;
    }
    this.anchorWall = this.clock.now();
    this.notify();
    this.pump();
  }
  pause() {
    if (this.disposed || this.paused) return;
    this.anchorT = this.position();
    this.paused = true;
    this.cancelTimer();
    this.notify();
  }
  /** Pause, then hand exactly one more recorded packet to the decoder. */
  step() {
    if (this.disposed) return;
    this.pause();
    if (this.ended) this.resetTo(0, true);
    this.showNext();
  }
  /** Seek to the keyframe that opens the chunk holding `frame`. */
  seek(frame: number) {
    if (this.disposed || !Number.isFinite(frame)) return;
    this.restartAt(chunkIndexForFrame(this.index, frame));
  }
  setLoop(loop: boolean) {
    this.loop = loop;
    this.notify();
  }
  /** The studio's playback controls, handled locally (there is no command service). */
  command(action: string, payload?: unknown): boolean {
    if (action === "pause") this.pause();
    else if (action === "resume") this.play();
    else if (action === "step") this.step();
    else if (action === "seek") {
      const frame = (payload as { frame_index?: unknown } | undefined)?.frame_index;
      if (typeof frame !== "number") return false;
      this.seek(frame);
    } else return false;
    return true;
  }
  close() {
    this.disposed = true;
    this.cancelTimer();
    this.decoder.terminate();
    this.cache.clear();
    this.records = null;
    this.inflight = [];
  }

  private position() {
    return this.paused ? this.anchorT : this.anchorT + (this.clock.now() - this.anchorWall) / 1000;
  }
  private reanchor(t: number) {
    this.anchorT = t;
    this.anchorWall = this.clock.now();
  }
  private cancelTimer() {
    if (this.timer !== null) this.clock.cancel(this.timer);
    this.timer = null;
  }
  private notify() {
    this.options.onPlayback?.(this.state);
  }
  /**
   * Moves the cursor to the keyframe opening `chunk` and resets the decoder.
   * A seek (`discard`) also drops frames still being decoded for the old
   * position; a loop wrap lets them finish, since they precede the reset.
   */
  private resetTo(chunk: number, discard: boolean) {
    if (discard) this.generation++;
    this.cancelTimer();
    this.decoder.postMessage({ type: "reset" });
    this.chunk = chunk;
    this.pos = 0;
    this.records = null;
    this.ended = false;
    if (discard) {
      this.lastFrameT = null;
      this.cursorFrame = this.index.chunks[chunk].first_frame;
    }
    this.reanchor(this.index.chunks[chunk].start_s);
  }
  private restartAt(chunk: number) {
    this.resetTo(chunk, true);
    this.notify();
    // Paused: show the keyframe at the seek point without starting playback.
    if (this.paused) this.showNext();
    else this.pump();
  }
  /** Hands the packet under the cursor to the decoder while paused. */
  private showNext() {
    const generation = this.generation;
    void this.ensure(generation).then((ready) => {
      if (!ready || generation !== this.generation || !this.records || !this.paused) return;
      const record = this.records[this.pos];
      this.anchorT = record.t;
      this.emit(record);
      this.advance();
      this.notify();
    });
  }
  private load(chunk: number): Promise<ReplayRecord[]> {
    let pending = this.cache.get(chunk);
    if (!pending) {
      const url = new URL(this.index.chunks[chunk].file, this.base).href;
      pending = this.fetcher(url, { credentials: "same-origin" }).then(async (response) => {
        if (!response.ok) throw new Error(`Replay chunk ${chunk} returned ${response.status}`);
        return parseRecords(await gunzip(await this.readBody(response, chunk)));
      });
      pending.catch(() => {
        if (this.cache.get(chunk) === pending) this.cache.delete(chunk);
      });
      this.cache.set(chunk, pending);
      // Keep only the current chunk, the next one and the first (for looping).
      const keep = new Set([this.chunk, this.nextChunk(this.chunk), 0, chunk]);
      for (const key of [...this.cache.keys()]) if (!keep.has(key)) this.cache.delete(key);
    }
    return pending;
  }
  /** Reads a chunk body, reporting progress against the size index.json states. */
  private async readBody(response: Response, chunk: number): Promise<ArrayBuffer> {
    const total = this.index.chunks[chunk].bytes;
    const reader = response.body?.getReader();
    if (!reader || !(total > 0)) return response.arrayBuffer();
    const parts: Uint8Array[] = [];
    let received = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done || this.disposed) break;
      parts.push(value);
      received += value.byteLength;
      if (received > MAX_BODY_BYTES) throw new Error(`Replay chunk ${chunk} is too large`);
      this.progress.set(chunk, received);
      if (this.buffering && chunk === this.chunk) this.notify();
    }
    this.progress.delete(chunk);
    const out = new Uint8Array(received);
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.byteLength;
    }
    return out.buffer;
  }
  private nextChunk(chunk: number) {
    const next = chunk + 1;
    return next < this.index.chunks.length ? next : this.loop ? 0 : -1;
  }
  /** Loads the current chunk (prefetching the next); buffering holds the replay clock. */
  private async ensure(generation: number): Promise<boolean> {
    if (this.records) return true;
    const started = this.clock.now();
    let waited = !this.cache.has(this.chunk);
    if (waited) {
      this.buffering = true;
      this.notify();
    }
    try {
      const records = await this.load(this.chunk);
      if (this.disposed || generation !== this.generation) return false;
      this.records = records;
      this.retries = 0;
      this.buffering = false;
      const next = this.nextChunk(this.chunk);
      if (next >= 0) void this.load(next).catch(() => {});
      // Time spent waiting for data is not replay time.
      waited ||= this.clock.now() - started > 50;
      const first = records[this.pos]?.t ?? records[0].t;
      if (waited && !this.paused) this.reanchor(Math.min(this.position(), first));
      return true;
    } catch (error) {
      if (this.disposed || generation !== this.generation) return false;
      this.onStatus("error", error instanceof Error ? error.message : String(error));
      if (++this.retries <= MAX_RETRIES)
        this.timer = this.clock.schedule(() => {
          this.timer = null;
          if (generation !== this.generation) return;
          if (this.paused) void this.ensure(generation);
          else this.pump();
        }, RETRY_MS);
      return false;
    }
  }
  private pump = () => {
    this.timer = null;
    if (this.disposed || this.paused) return;
    const generation = this.generation;
    if (!this.records) {
      void this.ensure(generation).then((ready) => {
        if (ready && generation === this.generation) this.pump();
      });
      return;
    }
    for (let burst = 0; burst < 64; burst++) {
      if (this.paused || !this.records) break;
      const record = this.records[this.pos];
      const delay = record.t - this.position();
      if (delay > 0.001) {
        this.timer = this.clock.schedule(this.pump, delay * 1000);
        return;
      }
      if (delay < -MAX_LAG_S) this.reanchor(record.t);
      this.emit(record);
      if (!this.advance()) {
        this.notify();
        if (this.ended || this.paused) return;
        this.pump();
        return;
      }
    }
    this.notify();
    if (!this.paused) this.timer = this.clock.schedule(this.pump, 0);
  };
  /** Moves the cursor; false when the next packet is in another chunk or the replay ended. */
  private advance(): boolean {
    if (!this.records) return false;
    this.pos++;
    if (this.pos < this.records.length) return true;
    const next = this.chunk + 1;
    if (next < this.index.chunks.length) {
      this.chunk = next;
      this.pos = 0;
      this.records = null;
      return false;
    }
    if (this.loop) {
      // The last frame keeps its recorded interval before the replay wraps.
      // duration_s is the last record's t_s, so the last recorded interval stands in.
      const last = this.records[this.records.length - 1].t;
      const previous = this.records[this.records.length - 2]?.t ?? last;
      const gap = Math.min(
        MAX_LOOP_GAP_S,
        Math.max(MIN_LOOP_GAP_S, this.index.duration_s - last, last - previous),
      );
      const at = this.position();
      this.resetTo(0, false);
      this.reanchor(this.index.chunks[0].start_s - Math.max(0, gap - Math.max(0, at - last)));
      return false;
    }
    this.ended = true;
    this.anchorT = this.position();
    this.paused = true;
    this.pos = this.records.length - 1;
    return false;
  }
  private emit(record: ReplayRecord) {
    const buffer = record.packet.slice().buffer;
    const frame = this.index.chunks[this.chunk].first_frame + this.pos;
    this.inflight.push({ generation: this.generation, t: record.t, frame });
    this.cursorFrame = frame;
    this.decoder.postMessage({ type: "decode", buffer }, [buffer]);
  }
  private decoded(data: { type: string; frame?: SceneFrame; message?: string }) {
    const entry = this.inflight.shift();
    if (this.disposed || !entry || entry.generation !== this.generation) return;
    if (data.type === "frame" && data.frame) {
      this.lastFrameT = entry.t;
      this.onStatus("replay");
      this.onFrame(data.frame);
      return;
    }
    // A recorded stream cannot ask for a keyframe; the next chunk starts with one.
    this.dropped++;
    this.onStatus(
      "error",
      `Recorded frame ${entry.frame + 1} could not be decoded: ${data.message ?? "unknown error"}`,
    );
  }
}
