import type { Ack, Bootstrap, Metadata, SceneFrame } from "./types";
import { jsonFrame } from "../scene/protocol";
export async function requestJSON<T>(
  url: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(url, { credentials: "same-origin", ...init });
  let body: any;
  try {
    body = await response.json();
  } catch {
    throw new Error(`Service returned ${response.status}.`);
  }
  if (!response.ok)
    throw new Error(
      body.detail ?? body.reason ?? `Service returned ${response.status}.`,
    );
  return body as T;
}
export function sendCommand(
  bootstrap: Bootstrap,
  action: string,
  payload: unknown,
  revision = bootstrap.control_revision,
) {
  return requestJSON<Ack>("/api/v2/commands", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(bootstrap.csrf_token ? { "X-CSRF-Token": bootstrap.csrf_token } : {}),
    },
    body: JSON.stringify({
      request_id: crypto.randomUUID(),
      map_revision: bootstrap.map_revision,
      expected_revision: revision,
      action,
      payload,
    }),
  });
}
export type Connection =
  | "connecting"
  | "streaming"
  | "fallback"
  | "resync"
  | "offline";
export class SceneStream {
  private worker: Worker;
  private socket: WebSocket | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private fallback: ReturnType<typeof setInterval> | null = null;
  private disposed = false;
  private busy = false;
  private pending: ArrayBuffer | null = null;
  public dropped = 0;
  constructor(
    private url: string,
    private onFrame: (frame: SceneFrame) => void,
    private onStatus: (status: Connection, message?: string) => void,
  ) {
    this.worker = new Worker(
      new URL("../scene/decode.worker.ts", import.meta.url),
      { type: "module" },
    );
    this.worker.onmessage = (event) => {
      this.busy = false;
      if (this.disposed) return;
      if (event.data.type === "frame") {
        this.onStatus("streaming");
        this.onFrame(event.data.frame);
      } else {
        this.onStatus("resync", event.data.message);
        if (this.socket?.readyState === WebSocket.OPEN)
          this.socket.send(JSON.stringify({ type: "resync" }));
        this.pending = null;
      }
      if (this.pending) {
        const buffer = this.pending;
        this.pending = null;
        this.decode(buffer);
      }
    };
    this.connect();
  }
  private decode(buffer: ArrayBuffer) {
    if (this.busy) {
      this.pending = buffer;
      this.dropped++;
      return;
    }
    this.busy = true;
    this.worker.postMessage({ type: "decode", buffer }, [buffer]);
  }
  private connect() {
    if (this.disposed) return;
    this.onStatus("connecting");
    const url = new URL(this.url, window.location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    this.socket = new WebSocket(url);
    this.socket.binaryType = "arraybuffer";
    this.socket.onopen = () => {
      if (this.fallback) {
        clearInterval(this.fallback);
        this.fallback = null;
      }
      this.pending = null;
      this.worker.postMessage({ type: "reset" });
      this.onStatus("streaming");
    };
    this.socket.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) this.decode(event.data);
      else {
        try {
          const message = JSON.parse(event.data);
          if (message.message_type === "twin.frame")
            this.onFrame(jsonFrame(message));
        } catch {
          this.onStatus("resync", "Invalid stream message");
        }
      }
    };
    this.socket.onerror = () =>
      this.onStatus("offline", "Scene connection interrupted");
    this.socket.onclose = () => {
      if (this.disposed) return;
      this.onStatus("fallback", "Using periodic snapshots; reconnecting");
      if (!this.fallback)
        this.fallback = setInterval(() => void this.poll(), 2000);
      void this.poll();
      this.timer = setTimeout(() => this.connect(), 3000);
    };
  }
  private async poll() {
    try {
      const state = await requestJSON<Metadata & { ready?: boolean }>(
        "/api/v2/state",
      );
      if (
        !this.disposed &&
        state.ready !== false &&
        state.frame_id !== undefined
      )
        this.onFrame(jsonFrame(state));
    } catch (error) {
      if (!this.disposed) this.onStatus("offline", String(error));
    }
  }
  close() {
    this.disposed = true;
    this.socket?.close();
    this.worker.terminate();
    if (this.timer) clearTimeout(this.timer);
    if (this.fallback) clearInterval(this.fallback);
    this.pending = null;
  }
}
