// Small TypeScript ports of the repository's algorithms for the explainer labs.
// Constants mirror the Python defaults; the labs are 2D teaching versions.

export type P2 = [number, number];

// ---- Deskew (slam_and_twin/imu_deskew.py) ----------------------------------
/** Square room of half-width `half`; distance from `origin` along `angle` to the walls. */
export function rayToRoom(origin: P2, angle: number, half: number) {
  const dx = Math.cos(angle), dy = Math.sin(angle);
  let best = Infinity;
  for (const [d, o] of [[dx, origin[0]], [dy, origin[1]]] as const)
    for (const wall of [-half, half])
      if (Math.abs(d) > 1e-9) {
        const t = (wall - o) / d;
        if (t > 0 && t < best) best = t;
      }
  return best;
}

/** One 360° sweep while the sensor moves (speed m/s, turn rad/s) over `period` s.
 * Returns points in world coordinates, either deskewed (each point with its own
 * pose) or naive (every point placed with the end-of-sweep pose). */
export function sweep(speed: number, turn: number, corrected: boolean, beams = 360, period = 0.1, half = 3) {
  const pose = (t: number) => ({ x: -0.5 + speed * t, y: 0, yaw: turn * t });
  const end = pose(period), points: P2[] = [];
  for (let i = 0; i < beams; i++) {
    const t = (i / beams) * period, p = pose(t), local = (i / beams) * 2 * Math.PI;
    const range = rayToRoom([p.x, p.y], p.yaw + local, half);
    const q = corrected ? p : end;
    points.push([q.x + range * Math.cos(q.yaw + local), q.y + range * Math.sin(q.yaw + local)]);
  }
  return points;
}

// ---- Log-odds map (slam_and_twin/log_odds_octomap.py) ---------------------
export const LOG_ODDS = { free: -0.4, hit: 0.85, min: -4, max: 4, decay: 0.96 };

/** Grid cells crossed by a ray (Amanatides-Woo traversal), excluding the end cell. */
export function traverse(from: P2, to: P2): P2[] {
  let x = Math.floor(from[0]), y = Math.floor(from[1]);
  const ex = Math.floor(to[0]), ey = Math.floor(to[1]);
  const dx = to[0] - from[0], dy = to[1] - from[1];
  const sx = Math.sign(dx), sy = Math.sign(dy);
  let tx = dx ? ((sx > 0 ? x + 1 : x) - from[0]) / dx : Infinity;
  let ty = dy ? ((sy > 0 ? y + 1 : y) - from[1]) / dy : Infinity;
  const ddx = dx ? Math.abs(1 / dx) : Infinity, ddy = dy ? Math.abs(1 / dy) : Infinity;
  const cells: P2[] = [];
  for (let guard = 0; (x !== ex || y !== ey) && guard < 10000; guard++) {
    cells.push([x, y]);
    if (tx < ty) { x += sx; tx += ddx; } else { y += sy; ty += ddy; }
  }
  return cells;
}

export function applyBeam(grid: Float32Array, width: number, from: P2, to: P2) {
  for (const [x, y] of traverse(from, to))
    if (x >= 0 && y >= 0 && x < width && y < grid.length / width)
      grid[y * width + x] = Math.max(LOG_ODDS.min, grid[y * width + x] + LOG_ODDS.free);
  const ex = Math.floor(to[0]), ey = Math.floor(to[1]);
  if (ex >= 0 && ey >= 0 && ex < width && ey < grid.length / width)
    grid[ey * width + ex] = Math.min(LOG_ODDS.max, grid[ey * width + ex] + LOG_ODDS.hit);
}

/** Decay applied to stored evidence after `seconds` without new observations. */
export const decayFactor = (seconds: number) => LOG_ODDS.decay ** (seconds / 0.1);
export const probability = (logOdds: number) => 1 / (1 + Math.exp(-logOdds));

// ---- Eigen descriptors (perception_ml/eigen_descriptors.py) ---------------
type M3 = number[][];
/** Eigenvalues of a symmetric 3x3 matrix, descending, with the eigenvector of the smallest. */
export function eigenSymmetric3(input: M3) {
  const a = input.map((row) => [...row]);
  const v = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
  for (let sweepIndex = 0; sweepIndex < 50; sweepIndex++) {
    let off = 0;
    for (let p = 0; p < 3; p++) for (let q = p + 1; q < 3; q++) off += a[p][q] ** 2;
    if (off < 1e-20) break;
    for (let p = 0; p < 3; p++)
      for (let q = p + 1; q < 3; q++) {
        if (Math.abs(a[p][q]) < 1e-30) continue;
        const theta = (a[q][q] - a[p][p]) / (2 * a[p][q]);
        const t = Math.sign(theta || 1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
        const c = 1 / Math.sqrt(t * t + 1), s = t * c;
        for (let k = 0; k < 3; k++) {
          const akp = a[k][p], akq = a[k][q];
          a[k][p] = c * akp - s * akq; a[k][q] = s * akp + c * akq;
        }
        for (let k = 0; k < 3; k++) {
          const apk = a[p][k], aqk = a[q][k];
          a[p][k] = c * apk - s * aqk; a[q][k] = s * apk + c * aqk;
        }
        for (let k = 0; k < 3; k++) {
          const vkp = v[k][p], vkq = v[k][q];
          v[k][p] = c * vkp - s * vkq; v[k][q] = s * vkp + c * vkq;
        }
      }
  }
  const order = [0, 1, 2].sort((i, j) => a[j][j] - a[i][i]);
  const smallest = order[2];
  return { values: order.map((i) => a[i][i]), normal: [v[0][smallest], v[1][smallest], v[2][smallest]] };
}

export function descriptors(points: number[][]) {
  const n = points.length, mean = [0, 1, 2].map((k) => points.reduce((s, p) => s + p[k], 0) / n);
  const cov = [0, 1, 2].map((i) => [0, 1, 2].map((j) =>
    points.reduce((s, p) => s + (p[i] - mean[i]) * (p[j] - mean[j]), 0) / Math.max(1, n - 1) + (i === j ? 1e-6 : 0)));
  const { values: [l1, l2, l3], normal } = eigenSymmetric3(cov);
  return {
    linearity: (l1 - l2) / l1,
    planarity: (l2 - l3) / l1,
    scattering: l3 / l1,
    verticality: 1 - Math.abs(normal[2]),
    values: [l1, l2, l3],
  };
}

// ---- Risk and time to collision (tracking_and_navigation/risk_and_geofence.py)
export const RISK = { sigmaBase: 0.65, alphaFront: 0.75, alphaLat: 0.2, safetyRadius: 0.55, entityRadius: 0.35 };

/** First non-negative contact time of two constant-velocity spheres (sum of radii `radii`). */
export function timeToCollision(relativePosition: P2, relativeVelocity: P2, radii: number) {
  const [rx, ry] = relativePosition, [vx, vy] = relativeVelocity;
  const a = vx * vx + vy * vy, b = 2 * (rx * vx + ry * vy), c = rx * rx + ry * ry - radii * radii;
  if (c <= 0) return 0;
  if (a <= 1e-16 || b >= 0) return Infinity;
  const discriminant = b * b - 4 * a * c;
  if (discriminant < 0) return Infinity;
  const q = -0.5 * (b - Math.sqrt(discriminant));
  const result = q > 0 ? c / q : -b / (2 * a);
  return result >= 0 ? result : Infinity;
}

/** Directional Gaussian risk of one entity at `point`. */
export function entityRisk(point: P2, position: P2, velocity: P2) {
  const speed = Math.hypot(velocity[0], velocity[1]);
  const heading = speed > 1e-8 ? Math.atan2(velocity[1], velocity[0]) : 0;
  const dx = point[0] - position[0], dy = point[1] - position[1];
  const along = Math.cos(heading) * dx + Math.sin(heading) * dy;
  const across = -Math.sin(heading) * dx + Math.cos(heading) * dy;
  const sigmaAlong = along >= 0 ? RISK.sigmaBase + RISK.alphaFront * speed : RISK.sigmaBase;
  const sigmaAcross = RISK.sigmaBase + RISK.alphaLat * speed;
  return Math.exp(-0.5 * ((along / sigmaAlong) ** 2 + (across / sigmaAcross) ** 2));
}
