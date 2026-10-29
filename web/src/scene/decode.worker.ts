import { SceneAccumulator, ResyncRequired } from "./protocol";
const accumulator = new SceneAccumulator();
self.onmessage = (event: MessageEvent) => {
  if (event.data.type === "reset") {
    accumulator.reset();
    return;
  }
  try {
    const frame = accumulator.apply(event.data.buffer);
    const transfer = [
      ...new Set(
        Object.values(frame.arrays).map((a) => a.buffer as ArrayBuffer),
      ),
    ];
    self.postMessage({ type: "frame", frame }, { transfer });
  } catch (error) {
    self.postMessage({
      type: error instanceof ResyncRequired ? "resync" : "error",
      message: error instanceof Error ? error.message : "Scene decode failed",
    });
  }
};
