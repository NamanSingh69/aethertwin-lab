// Showcase landing page. Plate 1 draws a subset of the lab twin's measured
// surface points (tools/export_landing_points.py) on a 2D canvas, with the
// data's extent, metre ticks and range rings taken from the same file. The
// points and dimensions are real; the rotation, the height colouring and the
// opening sweep are presentation only, and the caption says so. No replay
// data is fetched on this page.
import "./landing.css";
import pointsUrl from "./twin-points.bin?url";
import meta from "./twin-points.json";

const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** Height ramp, floor to ceiling: slate, paper white, signal orange (the legend in landing.css matches). */
const RAMP: Array<[number, number, number]> = [
  [93, 124, 150],
  [150, 172, 190],
  [226, 228, 226],
  [244, 239, 228],
  [248, 196, 166],
  [255, 90, 43],
];
const BUCKETS = 14;
const RING_RADII_CM = [200, 400, 600, 800];
const MIN_CM = meta.min_cm as [number, number, number],
  MAX_CM = meta.max_cm as [number, number, number],
  SENSOR_CM = meta.sensor_cm as [number, number, number];
const metres = (cm: number) => (cm / 100).toFixed(1);

function rampColor(t: number, alpha: number) {
  const x = Math.min(0.9999, Math.max(0, t)) * (RAMP.length - 1),
    i = Math.floor(x),
    f = x - i,
    a = RAMP[i]!,
    b = RAMP[i + 1]!;
  return `rgba(${Math.round(a[0] + (b[0] - a[0]) * f)},${Math.round(a[1] + (b[1] - a[1]) * f)},${Math.round(a[2] + (b[2] - a[2]) * f)},${alpha})`;
}

interface Cloud {
  /** x, y, z in centimetres about the scene centre, sorted by height bucket. */
  xyz: Float32Array;
  /** Angle around the rig, 0..1, for the opening sweep. */
  sweep: Float32Array;
  /** First point index of each bucket, plus the total. */
  starts: Uint32Array;
  centre: [number, number, number];
  radius: number;
}

function buildCloud(raw: Int16Array): Cloud {
  const count = Math.floor(raw.length / 3),
    centre: [number, number, number] = [(MIN_CM[0] + MAX_CM[0]) / 2, (MIN_CM[1] + MAX_CM[1]) / 2, MAX_CM[2] * 0.4],
    bucketOf = new Uint8Array(count),
    counts = new Uint32Array(BUCKETS + 1);
  for (let i = 0; i < count; i++) {
    const bucket = Math.min(BUCKETS - 1, Math.max(0, Math.floor((raw[i * 3 + 2]! / MAX_CM[2]) * BUCKETS)));
    bucketOf[i] = bucket;
    counts[bucket + 1]!++;
  }
  const starts = new Uint32Array(BUCKETS + 1);
  for (let b = 0; b < BUCKETS; b++) starts[b + 1] = starts[b]! + counts[b + 1]!;
  const cursor = starts.slice(0, BUCKETS),
    xyz = new Float32Array(count * 3),
    sweep = new Float32Array(count);
  for (let i = 0; i < count; i++) {
    const at = cursor[bucketOf[i]!]!++,
      x = raw[i * 3]!,
      y = raw[i * 3 + 1]!;
    xyz[at * 3] = x - centre[0];
    xyz[at * 3 + 1] = y - centre[1];
    xyz[at * 3 + 2] = raw[i * 3 + 2]! - centre[2];
    sweep[at] = (Math.atan2(y - SENSOR_CM[1], x - SENSOR_CM[0]) + Math.PI) / (2 * Math.PI);
  }
  return { xyz, sweep, starts, centre, radius: Math.hypot(MAX_CM[0] - MIN_CM[0], MAX_CM[1] - MIN_CM[1]) / 2 };
}

function startScene(canvas: HTMLCanvasElement, cloud: Cloud) {
  const context = canvas.getContext("2d");
  if (!context) throw new Error("no 2D canvas");
  const ctx = context;
  let width = 0,
    height = 0,
    dpr = 1,
    yaw = -0.5,
    pitch = 0.62,
    visible = true,
    frame = 0,
    last = 0,
    idleSince = -1e9,
    dragging = false,
    began = 0;
  const colours = Array.from({ length: BUCKETS }, (_, b) => rampColor(b / (BUCKETS - 1), b === 0 ? 0.5 : 0.9));
  const [cxw, cyw, czw] = cloud.centre,
    rig: [number, number, number] = [SENSOR_CM[0] - cxw, SENSOR_CM[1] - cyw, SENSOR_CM[2] - czw],
    floorZ = -czw,
    // The data's extent on the floor, about the scene centre.
    x0 = MIN_CM[0] - cxw,
    x1 = MAX_CM[0] - cxw,
    y0 = MIN_CM[1] - cyw,
    y1 = MAX_CM[1] - cyw,
    corners: Array<[number, number]> = [
      [x0, y0],
      [x1, y0],
      [x1, y1],
      [x0, y1],
    ];

  function resize() {
    const box = canvas.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    width = Math.max(1, Math.round(box.width * dpr));
    height = Math.max(1, Math.round(box.height * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
  }

  function draw(now: number) {
    // On a phone the readouts sit under the drawing, so it moves up and loses the ring labels.
    const small = width / dpr < 560,
      cx = width * 0.5,
      cy = height * (small ? 0.4 : 0.55),
      distance = cloud.radius * 3,
      // The data's footprint spans a fixed share of the plate.
      focal = (Math.min(width * (small ? 0.44 : 0.33), height * (small ? 0.5 : 0.74)) / cloud.radius) * distance,
      cosY = Math.cos(yaw),
      sinY = Math.sin(yaw),
      cosP = Math.cos(pitch),
      sinP = Math.sin(pitch),
      reveal = reducedMotion ? 1 : Math.min(1, (now - began) / 2400),
      eased = 1 - (1 - reveal) ** 3;
    const project = (x: number, y: number, z: number): [number, number] => {
      const rx = x * cosY - y * sinY,
        ry = x * sinY + y * cosY,
        scale = focal / (ry * cosP - z * sinP + distance);
      return [cx + rx * scale, cy - (ry * sinP + z * cosP) * scale];
    };
    ctx.clearRect(0, 0, width, height);
    ctx.font = `500 ${10.5 * dpr}px "Plex Mono", Consolas, monospace`;
    ctx.textBaseline = "middle";

    // Range rings on the floor around the rig, every 2 m, labelled on one bearing.
    ctx.lineWidth = dpr;
    for (const radius of RING_RADII_CM) {
      ctx.beginPath();
      for (let step = 0; step <= 96; step++) {
        const angle = (step / 96) * Math.PI * 2,
          [px, py] = project(rig[0] + Math.cos(angle) * radius, rig[1] + Math.sin(angle) * radius, floorZ);
        if (step === 0) ctx.moveTo(px, py);
        else ctx.lineTo(px, py);
      }
      ctx.strokeStyle = "rgba(139,150,163,0.2)";
      ctx.stroke();
      if (small) continue;
      const [lx, ly] = project(rig[0] + radius, rig[1], floorZ);
      ctx.fillStyle = "rgba(139,150,163,0.75)";
      ctx.textAlign = "left";
      ctx.fillText(`${radius / 100} m`, lx + 5 * dpr, ly);
    }

    // Extent of the data: a dashed outline with a tick every metre and the two side lengths.
    const screen = corners.map(([x, y]) => project(x, y, floorZ));
    ctx.setLineDash([4 * dpr, 4 * dpr]);
    ctx.strokeStyle = "rgba(223,228,234,0.34)";
    ctx.beginPath();
    screen.forEach(([px, py], index) => (index ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
    ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);
    // Label the two edges nearest the viewer (lowest on screen), one per axis.
    const edges = corners.map((from, index) => {
      const to = corners[(index + 1) % 4]!,
        a = screen[index]!,
        b = screen[(index + 1) % 4]!;
      return { from, to, a, b, alongX: from[1] === to[1], low: (a[1] + b[1]) / 2 };
    });
    for (const alongX of [true, false]) {
      const edge = edges.filter((e) => e.alongX === alongX).sort((p, q) => q.low - p.low)[0]!,
        length = alongX ? x1 - x0 : y1 - y0,
        // Outward normal in the floor plane, for the ticks.
        nx = alongX ? 0 : Math.sign(edge.from[0]) || 1,
        ny = alongX ? Math.sign(edge.from[1]) || 1 : 0;
      ctx.strokeStyle = "rgba(223,228,234,0.5)";
      ctx.beginPath();
      for (let m = 0; m <= Math.floor(length / 100); m++) {
        const t = (m * 100) / length,
          wx = edge.from[0] + (edge.to[0] - edge.from[0]) * t,
          wy = edge.from[1] + (edge.to[1] - edge.from[1]) * t,
          reach = m % 5 === 0 ? 34 : 16,
          [ax, ay] = project(wx, wy, floorZ),
          [bx, by] = project(wx + nx * reach, wy + ny * reach, floorZ);
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
      }
      ctx.stroke();
      const [mx, my] = project(
        (edge.from[0] + edge.to[0]) / 2 + nx * 95,
        (edge.from[1] + edge.to[1]) / 2 + ny * 95,
        floorZ,
      );
      ctx.fillStyle = "rgba(223,228,234,0.92)";
      ctx.textAlign = "center";
      ctx.fillText(`${metres(length)} m`, mx, my);
    }

    // The measured points.
    ctx.globalCompositeOperation = "lighter";
    const { xyz, sweep, starts } = cloud;
    for (let bucket = 0; bucket < BUCKETS; bucket++) {
      ctx.fillStyle = colours[bucket]!;
      for (let i = starts[bucket]!, end = starts[bucket + 1]!; i < end; i++) {
        if (sweep[i]! > eased) continue;
        const x = xyz[i * 3]!,
          y = xyz[i * 3 + 1]!,
          z = xyz[i * 3 + 2]!,
          rx = x * cosY - y * sinY,
          ry = x * sinY + y * cosY,
          depth = ry * cosP - z * sinP + distance;
        if (depth < 60) continue;
        const scale = focal / depth,
          size = Math.max(dpr, Math.min(3 * dpr, scale * 6.5));
        ctx.fillRect(cx + rx * scale - size / 2, cy - (ry * sinP + z * cosP) * scale - size / 2, size, size);
      }
    }
    ctx.globalCompositeOperation = "source-over";

    // Where the cart stood.
    const [sx, sy] = project(rig[0], rig[1], rig[2]);
    ctx.fillStyle = "#ff5a2b";
    ctx.beginPath();
    ctx.arc(sx, sy, 3.5 * dpr, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#ff5a2b";
    ctx.lineWidth = dpr;
    ctx.beginPath();
    ctx.moveTo(sx, sy - 6 * dpr);
    ctx.lineTo(sx, sy - 30 * dpr);
    ctx.stroke();
    ctx.fillStyle = "#ffb499";
    ctx.textAlign = "center";
    ctx.fillText("CART", sx, sy - 38 * dpr);
    return reveal < 1;
  }

  function tick(now: number) {
    frame = 0;
    // The idle rotation is slow; 30 frames a second is plenty and halves the work on phones.
    if (!dragging && now - last < 30) {
      frame = requestAnimationFrame(tick);
      return;
    }
    const dt = Math.min(0.05, (now - last) / 1000);
    last = now;
    if (!reducedMotion && !dragging && now - idleSince > 2500) yaw += dt * 0.06;
    const revealing = draw(now);
    if (visible && !document.hidden && (!reducedMotion || revealing)) frame = requestAnimationFrame(tick);
  }
  const schedule = () => {
    if (!frame) {
      last = performance.now();
      frame = requestAnimationFrame(tick);
    }
  };

  let lastX = 0,
    lastY = 0;
  canvas.addEventListener("pointerdown", (event) => {
    dragging = true;
    lastX = event.clientX;
    lastY = event.clientY;
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    yaw += (event.clientX - lastX) * 0.006;
    pitch = Math.min(1.3, Math.max(0.2, pitch + (event.clientY - lastY) * 0.004));
    lastX = event.clientX;
    lastY = event.clientY;
    schedule();
  });
  const release = () => {
    dragging = false;
    idleSince = performance.now();
    canvas.classList.remove("dragging");
  };
  canvas.addEventListener("pointerup", release);
  canvas.addEventListener("pointercancel", release);

  new ResizeObserver(() => {
    resize();
    schedule();
  }).observe(canvas);
  new IntersectionObserver(([entry]) => {
    visible = entry?.isIntersecting ?? true;
    if (visible) schedule();
  }).observe(canvas);
  document.addEventListener("visibilitychange", () => !document.hidden && schedule());
  // The canvas labels use the page's mono face; redraw once it has loaded.
  void document.fonts?.ready.then(schedule);
  resize();
  began = performance.now();
  schedule();
}

/** The plate's readouts come from the same metadata as the points. */
function fillReadouts() {
  const set = (id: string, text: string) => {
    const node = document.getElementById(id);
    if (node) node.textContent = text;
  };
  set("read-points", `${meta.count.toLocaleString("en")} of ${meta.total_measured_points.toLocaleString("en")}`);
  set("read-voxel", `${Math.round(meta.voxel_size_m * 100)} cm`);
  set("read-extent", [0, 1, 2].map((axis) => metres(MAX_CM[axis]! - MIN_CM[axis]!)).join(" × ") + " m");
  set("read-top", `${metres(MAX_CM[2])} m`);
}

async function loadScene() {
  const canvas = document.querySelector<HTMLCanvasElement>("#twin-canvas");
  if (!canvas) return;
  try {
    const response = await fetch(pointsUrl);
    if (!response.ok) throw new Error(String(response.status));
    startScene(canvas, buildCloud(new Int16Array(await response.arrayBuffer())));
  } catch {
    // The page reads fine without the drawing; the readouts and caption stay.
    canvas.hidden = true;
    const hint = document.getElementById("plate-hint");
    if (hint) hint.textContent = "Drawing unavailable";
  }
}

/** Draw the chart's bars once, when it first scrolls into view. Without this they are simply there. */
function animateChart() {
  const bars = document.querySelector<HTMLElement>(".bars");
  if (!bars || reducedMotion) return;
  const check = () => {
    if (bars.getBoundingClientRect().top > window.innerHeight * 0.9) return;
    bars.classList.add("drawn");
    window.removeEventListener("scroll", check);
  };
  window.addEventListener("scroll", check, { passive: true });
  check();
}

fillReadouts();
animateChart();
void loadScene();
