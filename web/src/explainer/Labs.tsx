import { useEffect, useMemo, useRef, useState } from "react";
import type { Audience, LabId } from "./content";
import {
  LOG_ODDS,
  RISK,
  applyBeam,
  decayFactor,
  descriptors,
  entityRisk,
  probability,
  rayToRoom,
  sweep,
  timeToCollision,
  type P2,
} from "./math";

type Draw = (ctx: CanvasRenderingContext2D, size: number, color: (name: string) => string) => void;

/** Square canvas sized to its container, redrawn when `draw` changes or the theme flips. */
function LabCanvas({ draw, label, onPoint }: { draw: Draw; label: string; onPoint?: (x: number, y: number) => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const [theme, setTheme] = useState(0);
  useEffect(() => {
    const media = window.matchMedia?.("(prefers-color-scheme: dark)");
    const bump = () => setTheme((v) => v + 1);
    media?.addEventListener?.("change", bump);
    window.addEventListener("resize", bump);
    return () => {
      media?.removeEventListener?.("change", bump);
      window.removeEventListener("resize", bump);
    };
  }, []);
  useEffect(() => {
    const canvas = ref.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const size = canvas.clientWidth || 320, ratio = window.devicePixelRatio || 1;
    canvas.width = canvas.height = Math.round(size * ratio);
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const styles = getComputedStyle(canvas);
    ctx.clearRect(0, 0, size, size);
    draw(ctx, size, (name) => styles.getPropertyValue(`--${name}`).trim() || "#888");
  }, [draw, theme]);
  return (
    <canvas
      ref={ref}
      className="lab-canvas"
      role="img"
      aria-label={label}
      onPointerDown={(event) => {
        if (!onPoint) return;
        const box = event.currentTarget.getBoundingClientRect();
        onPoint((event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height);
      }}
    />
  );
}

function Slider({ label, value, min, max, step, onChange, unit }: {
  label: string; value: number; min: number; max: number; step: number; unit: string; onChange: (value: number) => void;
}) {
  return (
    <label className="lab-slider">
      <span>{label}</span>
      <input type="range" min={min} max={max} step={step} value={value} onChange={(e) => onChange(Number(e.target.value))} />
      <output>{value.toFixed(step < 1 ? 1 : 0)} {unit}</output>
    </label>
  );
}

const world = (size: number, half: number) => (p: P2): P2 => [((p[0] + half) / (2 * half)) * size, ((half - p[1]) / (2 * half)) * size];

function DeskewLab({ audience }: { audience: Audience }) {
  const [speed, setSpeed] = useState(2), [turn, setTurn] = useState(3), [corrected, setCorrected] = useState(false);
  const points = useMemo(() => sweep(speed, turn, corrected), [speed, turn, corrected]);
  const draw: Draw = (ctx, size, color) => {
    const map = world(size, 3.6);
    ctx.strokeStyle = color("line"); ctx.lineWidth = 1; ctx.setLineDash([4, 4]);
    const [a, b] = [map([-3, 3]), map([3, -3])];
    ctx.strokeRect(a[0], a[1], b[0] - a[0], b[1] - a[1]); ctx.setLineDash([]);
    ctx.fillStyle = corrected ? color("good") : color("warn");
    for (const p of points) { const [x, y] = map(p); ctx.fillRect(x - 1.5, y - 1.5, 3, 3); }
  };
  return (
    <div className="lab">
      <LabCanvas draw={draw} label={`Top-down laser sweep of a square room, ${corrected ? "with" : "without"} motion correction`} />
      <div className="lab-controls">
        <Slider label="Sensor speed" value={speed} min={0} max={4} step={0.1} unit="m/s" onChange={setSpeed} />
        <Slider label="Turn rate" value={turn} min={0} max={6} step={0.1} unit="rad/s" onChange={setTurn} />
        <label className="lab-toggle">
          <input type="checkbox" checked={corrected} onChange={(e) => setCorrected(e.target.checked)} />
          Correct each point with its own pose
        </label>
        <p className="lab-note">
          {audience === "simple"
            ? "Dashed lines are the real walls. Move the sensor faster and the scan bends; turn correction on and the dots snap back onto the walls."
            : "One 0.1 s sweep, 360 beams. Naive: all points use the end-of-sweep pose. Corrected: point i uses the pose at t = i/360 · 0.1 s, as the IMU deskewer does with integrated SO(3) and velocity states."}
        </p>
      </div>
    </div>
  );
}

const GRID = 24;
function LogOddsLab({ audience }: { audience: Audience }) {
  const [grid, setGrid] = useState(() => new Float32Array(GRID * GRID));
  const [scans, setScans] = useState(0), [age, setAge] = useState(0), [person, setPerson] = useState(true);
  const sensor: P2 = [4.5, 12.5];
  const scan = () => {
    const next = grid.map((value) => value * decayFactor(age));
    for (let i = 0; i < 90; i++) {
      const angle = -Math.PI / 3 + (i / 89) * ((2 * Math.PI) / 3);
      let range = rayToRoom([sensor[0] - 12, sensor[1] - 12], angle, 11) * 0.999;
      if (person) {
        // A person is a disc of radius 1.2 cells centred in cell (14, 14).
        const ox = sensor[0] - 14.5, oy = sensor[1] - 14.5, dx = Math.cos(angle), dy = Math.sin(angle);
        const b = ox * dx + oy * dy, c = ox * ox + oy * oy - 1.44, disc = b * b - c;
        if (disc > 0 && -b - Math.sqrt(disc) > 0) range = Math.min(range, -b - Math.sqrt(disc) + 0.3);
      }
      applyBeam(next, GRID, sensor, [sensor[0] + range * Math.cos(angle), sensor[1] + range * Math.sin(angle)]);
    }
    setGrid(next); setScans((v) => v + 1); setAge(0);
  };
  const shown = useMemo(() => grid.map((value) => value * decayFactor(age)), [grid, age]);
  const draw: Draw = (ctx, size, color) => {
    const cell = size / GRID;
    for (let y = 0; y < GRID; y++)
      for (let x = 0; x < GRID; x++) {
        const value = shown[y * GRID + x];
        if (Math.abs(value) < 0.05) continue;
        ctx.globalAlpha = Math.min(1, Math.abs(value) / LOG_ODDS.max + 0.15);
        ctx.fillStyle = value > 0 ? color("warn") : color("accent");
        ctx.fillRect(x * cell, size - (y + 1) * cell, cell - 1, cell - 1);
      }
    ctx.globalAlpha = 1; ctx.fillStyle = color("ink");
    ctx.beginPath(); ctx.arc(sensor[0] * cell, size - sensor[1] * cell, cell * 0.4, 0, 2 * Math.PI); ctx.fill();
  };
  // Strongest evidence among the cells the person covers.
  const sample = Math.max(...[13, 14, 15].flatMap((y) => [13, 14, 15].map((x) => shown[y * GRID + x])));
  return (
    <div className="lab">
      <LabCanvas draw={draw} label={`Occupancy grid after ${scans} scans; orange cells are solid, blue cells are free`} />
      <div className="lab-controls">
        <div className="lab-buttons">
          <button onClick={scan}>Scan</button>
          <button onClick={() => { setGrid(new Float32Array(GRID * GRID)); setScans(0); setAge(0); }}>Reset</button>
        </div>
        <label className="lab-toggle">
          <input type="checkbox" checked={person} onChange={(e) => setPerson(e.target.checked)} />
          Person standing in the room
        </label>
        <Slider label="Time since last scan" value={age} min={0} max={10} step={0.5} unit="s" onChange={setAge} />
        <p className="lab-readout">
          Scans: {scans} · where the person stands: score {sample.toFixed(2)}, {(100 * probability(sample)).toFixed(0)}% occupied
        </p>
        <p className="lab-note">
          {audience === "simple"
            ? "Scan a few times, then remove the person and scan again: their cell flips from solid to empty as new evidence arrives. Move the time slider and old evidence fades."
            : `Per ray: crossed cells ${LOG_ODDS.free}, end cell +${LOG_ODDS.hit}, clamped to ±${LOG_ODDS.max}; stored values decay by ${LOG_ODDS.decay} per 0.1 s. Probability = 1 / (1 + e^-L).`}
        </p>
      </div>
    </div>
  );
}

const SHAPES = { wall: "Flat wall", pole: "Thin pole", blob: "Cluttered blob" } as const;
type Shape = keyof typeof SHAPES;
export function shapePoints(shape: Shape, noise: number) {
  // mulberry32: deterministic, so the scores do not jump between renders.
  let seed = 7;
  const random = () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return (((t ^ (t >>> 14)) >>> 0) / 4294967296) * 2 - 1;
  };
  return Array.from({ length: 121 }, (_, i) => {
    const n = () => random() * noise;
    // The wall is sampled on a jittered 11 x 11 grid, like a scanner's rows and columns.
    if (shape === "wall") return [((i % 11) / 5 - 1) * 0.8 + 0.02 * random() + n(), 0.01 * random() + n(), (Math.floor(i / 11) / 5 - 1) * 0.8 + 1 + 0.02 * random() + n()];
    if (shape === "pole") return [0.04 * random() + n(), 0.04 * random() + n(), random() * 0.9 + 1];
    return [0.45 * random() + n(), 0.45 * random() + n(), 0.45 * random() + 1 + n()];
  });
}
function ShapeLab({ audience }: { audience: Audience }) {
  const [shape, setShape] = useState<Shape>("wall"), [noise, setNoise] = useState(0.01);
  const points = useMemo(() => shapePoints(shape, noise), [shape, noise]);
  const result = useMemo(() => descriptors(points), [points]);
  const draw: Draw = (ctx, size, color) => {
    // Turned 35° and seen slightly from above, so a wall reads as a slanted sheet.
    const c = Math.cos(0.6), s = Math.sin(0.6), scale = size * 0.42;
    ctx.fillStyle = color("accent");
    for (const [x, y, z] of points) {
      const across = x * c - y * s, depth = x * s + y * c;
      ctx.fillRect(size / 2 + across * scale - 2, size / 2 - (z - 1 - 0.6 * depth) * scale - 2, 4, 4);
    }
  };
  const bars: Array<[string, number]> = [["Line-like", result.linearity], ["Flat", result.planarity], ["Scattered", result.scattering], ["Upright surface", result.verticality]];
  return (
    <div className="lab">
      <LabCanvas draw={draw} label={`${SHAPES[shape]} as a cloud of points`} />
      <div className="lab-controls">
        <div className="lab-buttons" role="group" aria-label="Shape">
          {(Object.keys(SHAPES) as Shape[]).map((key) => (
            <button key={key} aria-pressed={shape === key} onClick={() => setShape(key)}>{SHAPES[key]}</button>
          ))}
        </div>
        <Slider label="Sensor noise" value={noise} min={0} max={0.2} step={0.01} unit="m" onChange={setNoise} />
        <div className="lab-bars">
          {bars.map(([name, value]) => (
            <div className="lab-bar" key={name}>
              <span>{name}</span>
              <meter min={0} max={1} value={Math.max(0, Math.min(1, value))} aria-label={name} />
              <output>{value.toFixed(2)}</output>
            </div>
          ))}
        </div>
        <p className="lab-note">
          {audience === "simple"
            ? "The software looks at how the dots around each point are spread. A wall scores high on 'flat', a pole on 'line-like', clutter such as a plant on 'scattered'. Add noise and the clues get weaker, which is why a learned model combines them with camera colour."
            : `Covariance eigenvalues λ1 ≥ λ2 ≥ λ3 = ${result.values.map((v) => v.toExponential(1)).join(", ")}. Linearity (λ1−λ2)/λ1, planarity (λ2−λ3)/λ1, scattering λ3/λ1, verticality 1−|n_z|: four of the ten descriptors the network receives.`}
        </p>
      </div>
    </div>
  );
}

function RiskLab({ audience }: { audience: Audience }) {
  const [speed, setSpeed] = useState(1.2), [heading, setHeading] = useState(200), [position, setPosition] = useState<P2>([2.2, 1]);
  const half = 4;
  const velocity: P2 = [speed * Math.cos((heading * Math.PI) / 180), speed * Math.sin((heading * Math.PI) / 180)];
  const ttc = timeToCollision(position, velocity, RISK.safetyRadius + RISK.entityRadius);
  const draw: Draw = (ctx, size, color) => {
    const map = world(size, half), step = size / 48;
    ctx.fillStyle = color("warn");
    for (let i = 0; i < 48; i++)
      for (let j = 0; j < 48; j++) {
        const p: P2 = [-half + ((i + 0.5) / 48) * 2 * half, half - ((j + 0.5) / 48) * 2 * half];
        const r = entityRisk(p, position, velocity);
        if (r < 0.03) continue;
        ctx.globalAlpha = r * 0.85; ctx.fillRect(i * step, j * step, step + 0.5, step + 0.5);
      }
    ctx.globalAlpha = 1;
    const [rx, ry] = map([0, 0]), scale = size / (2 * half);
    ctx.strokeStyle = color("good"); ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(rx, ry, RISK.safetyRadius * scale, 0, 2 * Math.PI); ctx.stroke();
    ctx.fillStyle = color("good"); ctx.font = "12px sans-serif";
    ctx.fillText("robot", rx + RISK.safetyRadius * scale + 4, ry + 4);
    const [px, py] = map(position);
    ctx.fillStyle = color("ink"); ctx.beginPath(); ctx.arc(px, py, RISK.entityRadius * scale, 0, 2 * Math.PI); ctx.fill();
    const [hx, hy] = map([position[0] + velocity[0], position[1] + velocity[1]]);
    ctx.strokeStyle = color("ink"); ctx.beginPath(); ctx.moveTo(px, py); ctx.lineTo(hx, hy); ctx.stroke();
  };
  return (
    <div className="lab">
      <LabCanvas
        draw={draw}
        label={`Risk field around a person moving at ${speed.toFixed(1)} metres per second`}
        onPoint={(x, y) => setPosition([-half + x * 2 * half, half - y * 2 * half])}
      />
      <div className="lab-controls">
        <Slider label="Walking speed" value={speed} min={0} max={2.5} step={0.1} unit="m/s" onChange={setSpeed} />
        <Slider label="Heading" value={heading} min={0} max={359} step={1} unit="°" onChange={setHeading} />
        <p className="lab-readout" aria-live="polite">
          Time to contact: {Number.isFinite(ttc) ? `${ttc.toFixed(2)} s` : "no contact predicted"}
        </p>
        <p className="lab-note">
          {audience === "simple"
            ? "Click to place the person. The orange area is where caution is needed; it stretches ahead of them as they speed up. The timer says how long until they would reach the robot's safety circle if nobody changed course."
            : `σ ahead = ${RISK.sigmaBase} + ${RISK.alphaFront}·v, σ sideways = ${RISK.sigmaBase} + ${RISK.alphaLat}·v. Contact time solves |r + v·t| = ${(RISK.safetyRadius + RISK.entityRadius).toFixed(2)} m with a stable quadratic root; "no contact" holds only under constant velocity.`}
        </p>
      </div>
    </div>
  );
}

export function Lab({ id, audience }: { id: LabId; audience: Audience }) {
  if (id === "deskew") return <DeskewLab audience={audience} />;
  if (id === "logodds") return <LogOddsLab audience={audience} />;
  if (id === "shape") return <ShapeLab audience={audience} />;
  return <RiskLab audience={audience} />;
}
