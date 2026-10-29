# MASTER SYSTEM SPECIFICATION & AGENT PROMPT: Multi-Modal 3D Semantic Voxel Occupancy, Scene Flow, Autonomous Corridor Planning & Interactive Lab Digital Twin

## 1. ROLE & DIRECTIVE FOR THE CODING AGENT

You are a Principal Robotics, 3D Deep Learning, and Spatial Computing Architect. Your directive is to architect and implement the complete, end-to-end codebase for a **Real-Time Multi-Modal 3D Semantic Voxel Perception, Scene Flow Estimation, & Interactive Lab Digital Twin System**.

There are **no artificial time constraints or scope reductions**. Do not take architectural shortcuts, do not use stub functions, and do not omit mathematical rigor. Generate a comprehensive, modular, production-grade codebase with full mathematical implementations, multi-sensor fusion, deep neural architectures, probabilistic 3D mapping, multi-target tracking, trajectory planning, and interactive 3D visualization.

### Core System Capabilities to Implement
1. **Tri-Modal Sensor Ingestion & Hardware/Software Synchronization:**
   * **Primary 3D Sensor:** 3D LiDAR L2 (e.g., Unitree 4D LiDAR L2 / Livox non-repetitive hemispherical 3D LiDAR or Zenmuse L2 class sensor) providing 3D coordinates $(x, y, z)$, calibrated reflectivity/intensity $I$, multi-return echo indices $r_{\text{idx}} \in \{1, \dots, K\}$, and high-rate 6-Axis IMU telemetry $(\boldsymbol{\omega}, \mathbf{a})$.
   * **Secondary Near-Field Sensor:** Orbbec Gemini 330-series (**Gemini 333L / 335L**) Active/Passive Stereo RGB-D Depth Camera providing high-density aligned RGB + Depth $(1280 \times 800 \text{ @ } 30\text{--}60\text{ Hz})$ for near-field $(0.2\text{ m} \text{ to } 6.0\text{ m})$ sub-centimeter densification and cross-modal semantic feature painting.
   * **Optional Auxiliary 2D Planar Sensor:** Slamtec **RPLIDAR S2** (360° planar ToF laser scanner) for ground-plane drift clamping, 2D blind-spot safety ring enforcement, or pitch-servo actuated nodding 3D sweep accumulation.
2. **Continuous-Time IMU Deskewing & Multi-Station Pose-Graph SLAM:**
   * Remove ego-motion distortion from individual LiDAR sweeps via Lie Algebra $\mathfrak{se}(3)$ IMU pre-integration.
   * Support both **Stationary Command-Center Mode** (fixed tripod overlooking the lab) and **Mobile Egocentric Mode** (mounted on an AGV, quadruped, or drone via Point-to-Plane ICP odometry).
3. **High-Resolution Static Lab Digital Twin Reconstruction:**
   * Multi-station point cloud registration (Coarse FPFH + RANSAC global registration followed by Multi-Way Point-to-Plane ICP Pose-Graph Optimization).
   * Truncated Signed Distance Field (TSDF) & Screened Poisson Surface Reconstruction to build a watertight architectural mesh and high-speed 3D KD-Tree / Sparse Voxel Hash of the static laboratory.
4. **Probabilistic Multi-Return 3D Log-Odds Occupancy & Cross-Modal Voxelization:**
   * Sparse 64-bit spatial voxel hashing (`VoxelGrid`) with probabilistic log-odds occupancy updates ($L(V_k)$) that explicitly model **multi-return LiDAR beam penetration** (separating permeable foliage, glass, and dust from solid structural hazards).
   * **PointPainting / Cross-Modal Fusion:** Project 2D deep semantic feature maps and RGB colors from the Gemini 333L/335L camera directly onto 3D active voxels using calibrated intrinsics $\mathbf{K}$ and extrinsics $\mathbf{T}_{C \to L} \in SE(3)$.
5. **Deep Multi-Task 3D Neural Architecture (Semantics + 3D Scene Flow + Self-Supervised Pretraining):**
   * Hybrid 3D backbone combining **Sparse Voxel/Pillar Feature Encoding**, **PointNet++ Multi-Scale Set Abstraction**, **Range-Image Cross-Attention**, and **10D Weinmann Covariance Eigen-Descriptors**.
   * **Dual Task Heads:**
     * **Head A (3D Semantic Segmentation):** Classifies every active voxel/cluster into *Traversable Ground, Static Architecture, Human/Pedestrian, Dynamic Robotics/Equipment, Newly Placed Static Hazard, Thin Wire/Cable, or Permeable Foliage/Multi-Return Noise*.
     * **Head B (3D Scene Flow Vector Head):** Predicts pointwise/voxelwise 3D motion displacement vectors $\mathbf{f}_i = (\Delta x, \Delta y, \Delta z) \in \mathbb{R}^3$ between consecutive sweeps $\mathcal{P}_{t-1}$ and $\mathcal{P}_t$.
   * **Self-Supervised Point-MAE Module:** Masked Autoencoder pretraining pipeline allowing the team to pretrain the backbone on raw unlabeled L2 scans collected in the lab.
6. **3D Multi-Object Tracking (9-DoF Kalman Filter + Mahalanobis / 3D-GIoU Hungarian Matching):**
   * Constant-Acceleration (9-DoF) and Constant-Velocity (6-DoF) Kalman filtering with bipartite data association combining **Mahalanobis state distance**, **3D Generalized Intersection-over-Union (3D-GIoU)**, and **visual/geometric cosine similarity**.
7. **Digital Twin Analytics, Virtual 3D Geofencing & Autonomous Local Trajectory Planner:**
   * **Anisotropic Velocity-Weighted Collision Risk Field:** Computes directional collision risk potential $R(x, y, z)$ and exact Time-To-Collision ($\text{TTC}$) across the lab floor.
   * **3D Oriented Bounding Box (OBB) Virtual Geofencing:** Monitors sensitive lab zones (server racks, high-voltage benches, robotic cells) with real-time intrusion alarms.
   * **Persistent Temporal Traffic Heatmap:** Accumulates decayed human/robot trajectory densities across the lab floor.
   * **3D Safe Corridor Path Planner (A* + MPPI Trajectory Optimizer):** Computes real-time collision-free 3D trajectories through the lab's traversable voxel space.

---

## 2. SYSTEM ARCHITECTURE & MULTI-MODAL DATA PIPELINE

```
+===================================================================================================+
|                             LAYER 1: MULTI-SENSOR INGESTION & SYNC                                |
|                                                                                                   |
|  [3D LiDAR L2 (UDP: Point + Intensity + Multi-Return + IMU)] --+                                  |
|  [Orbbec Gemini 333L/335L (RGB + Depth + Camera IMU)] ---------+--> [Hardware/SW Ring-Buffer Sync]|
|  [Slamtec RPLIDAR S2 (2D 360° Planar Laser Slice)] ------------+                 |                |
|  [Multi-Sensor Synthetic Lab Simulator / .npz Replay Engine] --+                 |                |
+==================================================================================|================+
                                                                                   v
+===================================================================================================+
|                      LAYER 2: MOTION DESKEWING, SLAM & CROSS-MODAL FUSION                         |
|                                                                                                   |
|  [IMU Pre-Integration SE(3) Deskew] ---> [Point-to-Plane ICP Odometry / Pose-Graph Registration]  |
|                                                              |                                    |
|  [Gemini 333L RGB Stream] ---> [2D Semantic Feature CNN] ----+--> [Cross-Modal PointPainting]     |
|                                                              |    (X,Y,Z,I,Return + RGB + 2D Feat)|
+==============================================================|====================================+
                                                               v
+===================================================================================================+
|                   LAYER 3: STATIC DIGITAL TWIN & PROBABILISTIC SPATIAL DIFFING                    |
|                                                                                                   |
|  [Multi-Station TSDF / Poisson Lab Mesh] + [Static 3D cKDTree & Sparse Voxel Hash]                |
|                                                  |                                                |
|                  +-------------------------------+-------------------------------+                |
|                  v                                                               v                |
|   [Multi-Return Log-Odds OctoMap]                                 [Range-Adaptive Spatial Diff]   |
|   (Free, Solid Occupied, Permeable)                               (Isolates Foreground P_fg)      |
+==================================================================================|================+
                                                                                   v
+===================================================================================================+
|                     LAYER 4: DEEP 3D SEMANTICS, SCENE FLOW & KALMAN TRACKING                      |
|                                                                                                   |
|  [Sparse Voxel Hash + Eigen-Features] ---> [Hybrid 3D Backbone (Voxel-PointNet++ + Range Attn)]   |
|                                                       |                                           |
|                                  +--------------------+--------------------+                      |
|                                  v                                         v                      |
|                   [Head A: 3D Semantic Segmentation]         [Head B: 3D Scene Flow Vector]       |
|                                  |                                         |                      |
|                                  +--------------------+--------------------+                      |
|                                                       v                                           |
|                  [DBSCAN / Connected Voxel Clustering + 10D Weinmann Eigen-Invariants]            |
|                                                       |                                           |
|                                                       v                                           |
|                 [Mahalanobis + 3D-GIoU Hungarian Matcher + 9-DoF 3D Kalman Filter]                |
+=======================================================|===========================================+
                                                        v
+===================================================================================================+
|               LAYER 5: DIGITAL TWIN ANALYTICS, PATH PLANNING & 3D COMMAND CENTER                  |
|                                                                                                   |
|  +--> [Anisotropic Velocity-Weighted Risk Field R(x,y) & Exact Time-To-Collision (TTC)]           |
|  +--> [3D OBB/AABB Virtual Geofence Intrusion Engine & Automated Safety Alerts]                   |
|  +--> [Persistent Exponential-Decay Floor Traffic & Occupancy Heatmap H(x,y)]                     |
|  +--> [3D A* + MPPI Local Trajectory Planner (Safe Navigation Corridors)]                         |
|  +--> [Open3D Dark-Mode PBR/Shader Engine + OpenCV Telemetry HUD + WebSocket Telemetry Stream]    |
+===================================================================================================+
```

---

## 3. EXHAUSTIVE MATHEMATICAL FORMULATIONS

Every mathematical component below must be implemented explicitly in the codebase without approximation shortcuts.

### 3.1 Continuous-Time IMU Pre-Integration & $SE(3)$ Point Cloud Motion Deskewing
A 3D LiDAR L2 sweep over period $[t_0, t_1]$ (typically $\Delta T \approx 100\text{ ms}$) accumulates points at continuous timestamps $\tau_i \in [t_0, t_1]$. When the sensor moves, raw points suffer from motion distortion.

Given high-rate IMU measurements of angular velocity $\tilde{\boldsymbol{\omega}}_k$ and linear acceleration $\tilde{\mathbf{a}}_k$ in the body frame $\mathcal{F}_B$, with biases $\mathbf{b}_g, \mathbf{b}_a$ and gravity $\mathbf{g}_W = [0, 0, -9.80665]^T$:

$$\boldsymbol{\omega}_k = \tilde{\boldsymbol{\omega}}_k - \mathbf{b}_g, \qquad \mathbf{a}_k = \tilde{\mathbf{a}}_k - \mathbf{b}_a$$

Integrate the rigid-body state $(\mathbf{R}_B(\tau), \mathbf{v}_B(\tau), \mathbf{p}_B(\tau)) \in SO(3) \times \mathbb{R}^3 \times \mathbb{R}^3$ across IMU intervals $\delta t_k$:

$$\mathbf{R}(\tau + \delta t_k) = \mathbf{R}(\tau) \exp\left([\boldsymbol{\omega}_k \delta t_k]_\times\right)$$
$$\mathbf{v}(\tau + \delta t_k) = \mathbf{v}(\tau) + \left(\mathbf{R}(\tau)\mathbf{a}_k + \mathbf{g}_W\right)\delta t_k$$
$$\mathbf{p}(\tau + \delta t_k) = \mathbf{p}(\tau) + \mathbf{v}(\tau)\delta t_k + \frac{1}{2}\left(\mathbf{R}(\tau)\mathbf{a}_k + \mathbf{g}_W\right)\delta t_k^2$$

where $[\boldsymbol{\phi}]_\times$ is the $3 \times 3$ skew-symmetric cross-product matrix and $\exp([\boldsymbol{\phi}]_\times)$ is the closed-form Rodrigues formula:

$$\exp([\boldsymbol{\phi}]_\times) = \mathbf{I}_3 + \frac{\sin\Vert{}\boldsymbol{\phi}\Vert{}}{\Vert{}\boldsymbol{\phi}\Vert{}}[\boldsymbol{\phi}]_\times + \frac{1 - \cos\Vert{}\boldsymbol{\phi}\Vert{}}{\Vert{}\boldsymbol{\phi}\Vert{}^2}[\boldsymbol{\phi}]_\times^2$$

For each LiDAR point $\mathbf{p}_i^{\text{raw}}$ captured at relative timestamp $s_i = \frac{\tau_i - t_0}{t_1 - t_0} \in [0, 1]$, transform the point to the sweep-end reference frame $\mathcal{F}_{t_1}$ via $SE(3)$ interpolation:

$$\mathbf{T}_{t_1 \leftarrow \tau_i} = \mathbf{T}(t_1)^{-1} \mathbf{T}(\tau_i), \qquad \mathbf{p}_i^{\text{deskew}} = \mathbf{R}_{t_1 \leftarrow \tau_i} \mathbf{p}_i^{\text{raw}} + \mathbf{t}_{t_1 \leftarrow \tau_i}$$

---

### 3.2 Multi-Modal Calibration, Pinhole Projection & Cross-Modal PointPainting (Gemini 333L/335L + RPLIDAR S2 $\leftrightarrow$ LiDAR L2)

#### A. Gemini 333L/335L Stereo RGB-D to 3D LiDAR Frame
Given camera intrinsic matrix $\mathbf{K} \in \mathbb{R}^{3 \times 3}$, radial-tangential distortion coefficients $\mathbf{d} = (k_1, k_2, p_1, p_2, k_3)$, and rigid extrinsic matrix $\mathbf{T}_{L \leftarrow C} = [\mathbf{R}_{L \leftarrow C} \mid \mathbf{t}_{L \leftarrow C}] \in SE(3)$:
1. Back-project undistorted pixel $(u, v)$ with depth $z_C = D(u, v) \in [0.2\text{ m}, 6.0\text{ m}]$ into the camera optical frame $\mathcal{F}_C$:
   $$\mathbf{p}_C = \begin{bmatrix} \frac{(u - c_x) z_C}{f_x} \\ \frac{(v - c_y) z_C}{f_y} \\ z_C \end{bmatrix}$$
2. Transform $\mathbf{p}_C$ into the master LiDAR coordinate frame $\mathcal{F}_L$:
   $$\mathbf{p}_L = \mathbf{R}_{L \leftarrow C} \mathbf{p}_C + \mathbf{t}_{L \leftarrow C}$$

#### B. Forward Projection for PointPainting (Decorating 3D LiDAR Points with 2D CNN Features)
For any 3D LiDAR point $\mathbf{p}_L \in \mathbb{R}^3$, project it into the Gemini camera image plane using $\mathbf{T}_{C \leftarrow L} = \mathbf{T}_{L \leftarrow C}^{-1}$:

$$\mathbf{p}_C = [X_C, Y_C, Z_C]^T = \mathbf{R}_{C \leftarrow L} \mathbf{p}_L + \mathbf{t}_{C \leftarrow L}$$

If $Z_C > 0.2\text{ m}$, compute continuous image coordinates:

$$\tilde{u} = f_x \frac{X_C}{Z_C} + c_x, \qquad \tilde{v} = f_y \frac{Y_C}{Z_C} + c_y$$

If $(\tilde{u}, \tilde{v}) \in [0, W-1] \times [0, H-1]$ and the depth consistency check $\vert{}Z_C - D(\lfloor\tilde{u}\rceil, \lfloor\tilde{v}\rceil)\vert{} < \delta_{\text{occ}}$ holds (preventing foreground colors from bleeding onto background walls), bilinearly sample the 2D RGB values $[R, G, B]$ and 2D semantic feature vector $\mathbf{f}_{\text{2D}}(\tilde{u}, \tilde{v}) \in \mathbb{R}^{C_{\text{2D}}}$ and append them to the LiDAR point feature vector:

$$\mathbf{x}_i^{\text{painted}} = \left[ x_i, y_i, z_i, I_i, r_{\text{idx}, i}, R_i, G_i, B_i, \mathbf{f}_{\text{2D}}(\tilde{u}_i, \tilde{v}_i)^T \right]^T$$

#### C. RPLIDAR S2 2D Planar Slice Integration
Given 2D polar laser readings $\{(r_m, \theta_m)\}_{m=1}^{M_{\text{2D}}}$ from the RPLIDAR S2 mounted at extrinsic pose $\mathbf{T}_{L \leftarrow S2}(\phi_{\text{pitch}}) \in SE(3)$ (where $\phi_{\text{pitch}}$ is either fixed at $0^\circ$ for a horizontal safety ring or driven by a nodding gimbal encoder):

$$\mathbf{p}_{S2, m} = \begin{bmatrix} r_m \cos\theta_m \\ r_m \sin\theta_m \\ 0 \end{bmatrix}, \qquad \mathbf{p}_{L, m} = \mathbf{R}_{L \leftarrow S2}(\phi_{\text{pitch}}) \mathbf{p}_{S2, m} + \mathbf{t}_{L \leftarrow S2}$$

---

### 3.3 Multi-Return Beam Penetration & Probabilistic 3D Log-Odds Voxel Occupancy
High-end LiDARs like the L2 emit pulses that record up to $K_{\text{ret}}$ returns per laser shot when passing through semi-transparent or porous media (foliage, chain-link fences, glass, rain/dust).

Let a laser pulse originate at sensor origin $\mathbf{o}_L$ along unit ray direction $\mathbf{d}_j$, producing ordered returns at distances $0 < \rho_{j,1} < \rho_{j,2} < \dots < \rho_{j, K_j}$ with intensities $I_{j,1}, \dots, I_{j, K_j}$.

1. **Voxel Ray-Traversal (3D Amanatides-Woo Fast Voxel Traversal):**
   * All voxels along ray $\mathbf{d}_j$ for distance $\rho \in [0, \rho_{j,1})$ are observed as **Free Space**.
   * For intermediate returns $m \in \{1, \dots, K_j - 1\}$ at position $\mathbf{p}_{j,m} = \mathbf{o}_L + \rho_{j,m}\mathbf{d}_j$, the beam partially reflected and continued forward. Therefore, voxel $V(\mathbf{p}_{j,m})$ is updated with a **Permeable / Soft-Obstacle Increment** $\ell_{\text{perm}}$, and compute its **Penetration Ratio** $\kappa_{j,m}$:
     $$\kappa_{j,m} = \frac{K_j - m}{K_j} \in (0, 1)$$
   * The final return $m = K_j$ at $\mathbf{p}_{j, K_j}$ (or a single-return hit $K_j = 1$) updates voxel $V(\mathbf{p}_{j, K_j})$ with a **Solid Occupied Increment** $\ell_{\text{occ}}$.

2. **Bayesian Log-Odds Voxel State Update:**
   Maintain for each active voxel $V_k$ a log-odds occupancy value $L_t(V_k) = \log \frac{P(V_k \text{ occupied})}{1 - P(V_k \text{ occupied})}$ clamped to $[L_{\min}, L_{\max}]$:

   $$L_t(V_k) = \text{clip}\left( \gamma_{\text{voxel}} L_{t-1}(V_k) + \Delta \ell(V_k, z_t), \; L_{\min}, \; L_{\max} \right)$$

   where:
   * $\Delta \ell_{\text{occ}} = +0.85$ (solid obstacle hit, $K_j = 1$ or final echo with high intensity)
   * $\Delta \ell_{\text{perm}} = +0.25$ (intermediate multi-return hit; flagged with permeability score $\bar{\kappa}(V_k)$)
   * $\Delta \ell_{\text{free}} = -0.40$ (traversed by beam before a subsequent return)
   * $\gamma_{\text{voxel}} \in [0.95, 1.0]$ controls temporal decay for dynamic obstacles.

---

### 3.4 Multi-Way Point-to-Plane ICP & Pose-Graph Optimization for Static Digital Twin Reconstruction
To construct a complete, occlusion-free 3D Digital Twin of the lab from $S$ scan stations $\{\mathcal{P}_1, \dots, \mathcal{P}_S\}$:

1. **Pairwise Point-to-Plane ICP:** For overlapping scan pair $(\mathcal{P}_a, \mathcal{P}_b)$, solve iteratively via Gauss-Newton over twist vector $\boldsymbol{\xi} = [\boldsymbol{\omega}^T, \boldsymbol{\nu}^T]^T \in \mathfrak{se}(3)$:

   $$\boldsymbol{\xi}^* = \arg\min_{\boldsymbol{\xi}} \sum_{(\mathbf{p}_i, \mathbf{q}_i) \in \mathcal{C}_{ab}} \rho_{\text{Huber}}\left( \left( (\exp([\boldsymbol{\xi}]_\times)\mathbf{p}_i - \mathbf{q}_i) \cdot \mathbf{n}_{q_i} \right)^2 \right)$$

   Linearizing for small $\boldsymbol{\xi}$ gives the $6 \times 6$ linear system $\mathbf{J}^T \mathbf{W} \mathbf{J} \boldsymbol{\xi} = -\mathbf{J}^T \mathbf{W} \mathbf{r}$, where the $i$-th row of Jacobian $\mathbf{J}_i \in \mathbb{R}^{1 \times 6}$ is:

   $$\mathbf{J}_i = \left[ (\mathbf{p}_i \times \mathbf{n}_{q_i})^T, \quad \mathbf{n}_{q_i}^T \right], \qquad r_i = (\mathbf{p}_i - \mathbf{q}_i) \cdot \mathbf{n}_{q_i}$$

2. **Global Pose-Graph Optimization:** Optimize all station poses $\{\mathbf{T}_1, \dots, \mathbf{T}_S\} \in SE(3)^S$ simultaneously to eliminate loop-closure drift:

   $$\min_{\{\mathbf{T}_a\}} \sum_{(a, b) \in \mathcal{E}} \left\Vert{} \log\left( \mathbf{T}_{ab}^{-1} \mathbf{T}_a^{-1} \mathbf{T}_b \right)^\vee \right\Vert{}_{\boldsymbol{\Lambda}_{ab}}^2$$

---

### 3.5 Range-Adaptive 3D KD-Tree Spatial Diffing & Floor Plane Alignment
1. **RANSAC Floor Alignment:** Fit plane $\Pi_{\text{floor}}: \mathbf{n}_f^T \mathbf{p} + d_f = 0$ ($\Vert{}\mathbf{n}_f\Vert{}_2 = 1$). Compute alignment rotation $\mathbf{R}_{\text{level}}$ from axis $\mathbf{u} = \frac{\mathbf{n}_f \times \mathbf{e}_z}{\Vert{}\mathbf{n}_f \times \mathbf{e}_z\Vert{}_2}$ and angle $\theta = \arccos(\mathbf{n}_f \cdot \mathbf{e}_z)$:

   $$\mathbf{R}_{\text{level}} = \mathbf{I}_3 \cos\theta + [\mathbf{u}]_\times \sin\theta + \mathbf{u}\mathbf{u}^T (1 - \cos\theta)$$

   Transform the static map and all incoming sweeps by $\mathbf{p} \leftarrow \mathbf{R}_{\text{level}}\mathbf{p} + [0, 0, d_f]^T$ so the lab floor lies strictly on $Z = 0$.
2. **Range-Adaptive KD-Tree Foreground Isolation:** Given static lab KD-Tree $\mathcal{T}_{\text{static}}$, an incoming point $\mathbf{p}_i$ at range $r_i = \Vert{}\mathbf{p}_i\Vert{}_2$ with nearest static neighbor distance $d_{\text{nn}}(\mathbf{p}_i)$ is classified as active foreground $\mathcal{P}_{\text{fg}}$ if:

   $$d_{\text{nn}}(\mathbf{p}_i) > \tau_0 + \beta_{\text{div}} r_i \quad \land \quad z_i > \epsilon_{\text{floor}}$$

   where $\tau_0 = 0.14\text{ m}$, $\beta_{\text{div}} = 0.008\text{ m/m}$, and $\epsilon_{\text{floor}} = 0.06\text{ m}$.

---

### 3.6 64-Bit Spatial Voxel Hashing & 10D Weinmann Covariance Eigen-Descriptors
1. **64-Bit Spatial Hash:** Quantize $\mathbf{p}_i = (x_i, y_i, z_i)$ into integer voxel indices $\mathbf{v}_i = \lfloor \mathbf{p}_i / s \rfloor \in \mathbb{Z}^3$ and hash via large primes:

   $$h(\mathbf{v}_i) = \left( (v_x \cdot 73856093) \oplus (v_y \cdot 19349663) \oplus (v_z \cdot 83492791) \right) \bmod 2^{63}$$

2. **Covariance Eigen-Invariants:** For any 3D point cluster or local voxel neighborhood $\mathcal{C}_k = \{\mathbf{p}_1, \dots, \mathbf{p}_m\}$, compute the regularized covariance matrix $\boldsymbol{\Sigma}_k \in \mathbb{R}^{3 \times 3}$ and its sorted eigenvalues $\lambda_1 \ge \lambda_2 \ge \lambda_3 > 0$:

   $$\boldsymbol{\Sigma}_k = \frac{1}{m - 1}\sum_{i=1}^{m}(\mathbf{p}_i - \boldsymbol{\mu}_k)(\mathbf{p}_i - \boldsymbol{\mu}_k)^T + 10^{-6}\mathbf{I}_3$$

   Extract the full set of geometric spectral invariants:
   * **Linearity (wires, cables, tripod legs, poles):** $L_\lambda = \frac{\lambda_1 - \lambda_2}{\lambda_1}$
   * **Planarity (walls, monitors, cabinet surfaces):** $P_\lambda = \frac{\lambda_2 - \lambda_3}{\lambda_1}$
   * **Sphericity / Scattering (bushes, foliage, chaotic multi-return scatter):** $S_\lambda = \frac{\lambda_3}{\lambda_1}$
   * **Omnivariance:** $O_\lambda = (\lambda_1 \lambda_2 \lambda_3)^{1/3}$
   * **Anisotropy:** $A_\lambda = \frac{\lambda_1 - \lambda_3}{\lambda_1}$
   * **Eigenentropy:** $E_\lambda = -\sum_{j=1}^{3} \tilde{\lambda}_j \ln(\tilde{\lambda}_j)$ where $\tilde{\lambda}_j = \frac{\lambda_j}{\lambda_1 + \lambda_2 + \lambda_3}$
   * **Surface Variation (Curvature):** $C_\lambda = \frac{\lambda_3}{\lambda_1 + \lambda_2 + \lambda_3}$
   * **Verticality:** $V_\lambda = 1 - \vert{}\mathbf{e}_z \cdot \mathbf{u}_3\vert{}$, where $\mathbf{u}_3$ is the eigenvector corresponding to $\lambda_3$.

---

### 3.7 Multi-Task 3D Neural Architecture (Semantics + Scene Flow + Point-MAE Pretraining)

#### A. Voxel Feature Encoding (VFE) & Hierarchical PointNet++ Set Abstraction
For each active voxel $V_k$ containing points $\{\mathbf{x}_1, \dots, \mathbf{x}_{N_k}\}$ where $\mathbf{x}_i = [x_i, y_i, z_i, I_i, r_{\text{idx}, i}, R_i, G_i, B_i]^T$:
1. Augment each point with its offset from the voxel centroid $\boldsymbol{\mu}_k$:
   $$\hat{\mathbf{x}}_i = \left[ \mathbf{x}_i^T, \; (x_i - \mu_{kx}), \; (y_i - \mu_{ky}), \; (z_i - \mu_{kz}), \; \Vert{}\mathbf{p}_i - \boldsymbol{\mu}_k\Vert{}_2 \right]^T \in \mathbb{R}^{12}$$
2. Pass through shared linear + BatchNorm + GELU layers followed by symmetric max-pooling across points inside $V_k$ to yield voxel embedding $\mathbf{f}_k^{(0)} \in \mathbb{R}^{D}$.
3. Apply **Multi-Scale Set Abstraction (PointNet++) & Sparse K-NN Graph Attention** over voxel centroids $\{\boldsymbol{\mu}_k\}$ between consecutive frames $t-1$ and $t$:
   $$\mathbf{h}_k^{(\ell)} = \max_{j \in \mathcal{N}(k)} \text{MLP}^{(\ell)}\left( \left[ \mathbf{h}_k^{(\ell-1)} \;\Vert{}\; \mathbf{h}_j^{(\ell-1)} - \mathbf{h}_k^{(\ell-1)} \;\Vert{}\; \text{PE}(\boldsymbol{\mu}_j - \boldsymbol{\mu}_k) \;\Vert{}\; \mathbf{g}_k \right] \right)$$
   where $\mathbf{g}_k$ is the Weinmann eigen-invariant vector and $\text{PE}(\cdot)$ is a sinusoidal 3D positional encoding.

#### B. Cross-Frame Temporal Flow Cost Volume Layer
To estimate 3D Scene Flow $\hat{\mathbf{f}}_k = (\Delta x_k, \Delta y_k, \Delta z_k)$ between frame $t-1$ and frame $t$, compute a learnable 3D correlation embedding between voxel $k$ at frame $t$ and its local spatial neighbors $j \in \mathcal{N}_{t-1}(\boldsymbol{\mu}_k^{(t)})$ in frame $t-1$:

$$\mathbf{e}_{k}^{\text{flow}} = \sum_{j \in \mathcal{N}_{t-1}(k)} \text{Softmax}_j\left( \frac{\langle \mathbf{W}_Q \mathbf{h}_k^{(t)}, \mathbf{W}_K \mathbf{h}_j^{(t-1)} \rangle}{\sqrt{D}} \right) \cdot \text{MLP}_{\text{flow}}\left( \left[ \mathbf{h}_j^{(t-1)} \;\Vert{}\; \boldsymbol{\mu}_k^{(t)} - \boldsymbol{\mu}_j^{(t-1)} \right] \right)$$

#### C. Multi-Task Loss Function
1. **Semantic Head Loss (Weighted Focal + Lovász-Softmax Loss):**
   $$\mathcal{L}_{\text{sem}} = -\sum_{c=1}^{C} \alpha_c (1 - p_{k,c})^\gamma y_{k,c} \log(p_{k,c}) + \mathcal{L}_{\text{Lovász}}(\mathbf{p}, \mathbf{y})$$
2. **3D Scene Flow Loss (Smooth-$L_1$ + Spatial Rigidity Consistency Regularizer):**
   $$\mathcal{L}_{\text{flow}} = \frac{1}{\vert{}\mathcal{V}_t\vert{}} \sum_{k \in \mathcal{V}_t} \Vert{}\hat{\mathbf{f}}_k - \mathbf{f}_k^*\Vert{}_{\text{Huber}} + \lambda_{\text{rigid}} \sum_{k \in \mathcal{V}_t} \sum_{j \in \mathcal{N}_{\text{same-cluster}}(k)} \Vert{}\hat{\mathbf{f}}_k - \hat{\mathbf{f}}_j\Vert{}_2^2$$
3. **Self-Supervised Point-MAE Pretraining Loss (Chamfer Distance on Masked Voxel Patches):**
   Mask $60\%$ of active voxel patches $\mathcal{V}_{\text{mask}}$ and reconstruct their local point coordinates $\hat{\mathcal{P}}_m$ from unmasked context tokens using a Transformer decoder, minimizing the bidirectional Chamfer Distance:
   $$\mathcal{L}_{\text{CD}}(\hat{\mathcal{P}}_m, \mathcal{P}_m) = \frac{1}{\vert{}\hat{\mathcal{P}}_m\vert{}}\sum_{\hat{\mathbf{p}} \in \hat{\mathcal{P}}_m} \min_{\mathbf{p} \in \mathcal{P}_m} \Vert{}\hat{\mathbf{p}} - \mathbf{p}\Vert{}_2^2 + \frac{1}{\vert{}\mathcal{P}_m\vert{}}\sum_{\mathbf{p} \in \mathcal{P}_m} \min_{\hat{\mathbf{p}} \in \hat{\mathcal{P}}_m} \Vert{}\mathbf{p} - \hat{\mathbf{p}}\Vert{}_2^2$$

---

### 3.8 9-DoF Constant-Acceleration 3D Kalman Filter & Mahalanobis + 3D-GIoU Hungarian Matching
Each tracked object $j$ maintains a 9-dimensional state vector $\mathbf{x}_j \in \mathbb{R}^9$ and $9 \times 9$ covariance matrix $\mathbf{P}_j$:

$$\mathbf{x}_j = \begin{bmatrix} x & y & z & v_x & v_y & v_z & a_x & a_y & a_z \end{bmatrix}^T$$

#### A. State Transition & Scene-Flow Augmented Observation Model
For elapsed time $\Delta t$, the state transition matrix $\mathbf{F}(\Delta t) \in \mathbb{R}^{9 \times 9}$ is:

$$\mathbf{F}(\Delta t) = \begin{bmatrix} \mathbf{I}_3 & \Delta t \mathbf{I}_3 & \frac{1}{2}\Delta t^2 \mathbf{I}_3 \\ \mathbf{0}_3 & \mathbf{I}_3 & \Delta t \mathbf{I}_3 \\ \mathbf{0}_3 & \mathbf{0}_3 & \mathbf{I}_3 \end{bmatrix}$$

Because the neural network predicts both cluster centroid $\boldsymbol{\mu}_k \in \mathbb{R}^3$ AND mean cluster scene-flow velocity $\mathbf{v}_{k}^{\text{flow}} = \frac{\bar{\mathbf{f}}_k}{\Delta t} \in \mathbb{R}^3$, the measurement vector is 6-dimensional $\mathbf{z}_k = [\boldsymbol{\mu}_k^T, (\mathbf{v}_k^{\text{flow}})^T]^T \in \mathbb{R}^6$, observed via:

$$\mathbf{H} = \begin{bmatrix} \mathbf{I}_3 & \mathbf{0}_3 & \mathbf{0}_3 \\ \mathbf{0}_3 & \mathbf{I}_3 & \mathbf{0}_3 \end{bmatrix} \in \mathbb{R}^{6 \times 9}$$

#### B. Multi-Cue Bipartite Association Cost Matrix
For track $j$ with predicted measurement $\hat{\mathbf{z}}_j = \mathbf{H}\hat{\mathbf{x}}_{j, t\vert{}t-1}$ and innovation covariance $\mathbf{S}_j = \mathbf{H}\mathbf{P}_{j, t\vert{}t-1}\mathbf{H}^T + \mathbf{R}$, and detection $k$ with measurement $\mathbf{z}_k$, 3D bounding box $B_k$, and feature embedding $\mathbf{h}_k$:

1. **Squared Mahalanobis Distance:**
   $$d_{\text{Maha}}^2(j, k) = (\mathbf{z}_k - \hat{\mathbf{z}}_j)^T \mathbf{S}_j^{-1} (\mathbf{z}_k - \hat{\mathbf{z}}_j)$$
2. **3D Generalized IoU (3D-GIoU):**
   $$\text{GIoU}_{\text{3D}}(B_j, B_k) = \frac{\text{Vol}(B_j \cap B_k)}{\text{Vol}(B_j \cup B_k)} - \frac{\text{Vol}(B_{\text{enclosing}}(B_j, B_k)) - \text{Vol}(B_j \cup B_k)}{\text{Vol}(B_{\text{enclosing}}(B_j, B_k))}$$
3. **Feature Cosine Distance:**
   $$d_{\text{cos}}(j, k) = 1 - \frac{\langle \mathbf{h}_j, \mathbf{h}_k \rangle}{\Vert{}\mathbf{h}_j\Vert{}_2 \Vert{}\mathbf{h}_k\Vert{}_2 + 10^{-6}}$$

Combine into the total Hungarian assignment cost $C_{j,k}$:

$$C_{j,k} = w_1 \sqrt{d_{\text{Maha}}^2(j, k)} + w_2 \left(1 - \text{GIoU}_{\text{3D}}(B_j, B_k)\right) + w_3 d_{\text{cos}}(j, k)$$

Gate pairs using the $\chi^2_{6, 0.99} = 16.81$ Mahalanobis threshold before accepting matches.

---

### 3.9 Anisotropic Velocity-Weighted Risk Field, Exact 3D TTC & Safe Corridor Path Planning

#### A. Anisotropic Velocity-Ellipsoid Risk Potential Field
For any floor coordinate $\mathbf{q} = (x, y, 0)$ and tracked entity $k$ at $\mathbf{p}_k = (x_k, y_k, 0)$ moving with horizontal velocity $\mathbf{v}_k = (v_{kx}, v_{ky})$ and speed $s_k = \Vert{}\mathbf{v}_k\Vert{}_2$:
1. Define local frame rotation $\mathbf{R}(\psi_k) \in SO(2)$ aligned with heading angle $\psi_k = \text{atan2}(v_{ky}, v_{kx})$.
2. Transform displacement $\mathbf{d} = \mathbf{q}_{xy} - \mathbf{p}_{k, xy}$ into the obstacle's heading frame: $[d_\parallel, d_\perp]^T = \mathbf{R}(\psi_k)^T \mathbf{d}$.
3. Define asymmetric longitudinal and lateral standard deviations (stretching forward in the direction of motion):
   $$\sigma_\parallel(s_k) = \begin{cases} \sigma_0 + \alpha_{\text{front}} s_k & \text{if } d_\parallel \ge 0 \\ \sigma_0 & \text{if } d_\parallel < 0 \end{cases}, \qquad \sigma_\perp(s_k) = \sigma_0 + \alpha_{\text{lat}} s_k$$
4. Compute risk field intensity $R(\mathbf{q}) \in [0, 1]$:
   $$r_k(\mathbf{q}) = \exp\left( -\frac{1}{2}\left( \frac{d_\parallel^2}{\sigma_\parallel(s_k)^2} + \frac{d_\perp^2}{\sigma_\perp(s_k)^2} \right) \right), \qquad R(\mathbf{q}) = 1 - \prod_{k=1}^{K} \left(1 - r_k(\mathbf{q})\right)$$

#### B. Exact Quadratic Ray-Sphere Time-To-Collision (TTC)
Given a protected zone or robot at position $\mathbf{p}_0$ with safety radius $r_{\text{safe}}$, and obstacle $k$ at relative position $\mathbf{r} = \mathbf{p}_k - \mathbf{p}_0$ moving with relative velocity $\mathbf{v} = \mathbf{v}_k - \mathbf{v}_0$ and combined radius $R_{\text{comb}} = r_{\text{safe}} + r_k$:
Solve $\|\mathbf{r} + t \mathbf{v}\|_2^2 = R_{\text{comb}}^2 \implies (\mathbf{v}\cdot\mathbf{v})t^2 + 2(\mathbf{r}\cdot\mathbf{v})t + (\mathbf{r}\cdot\mathbf{r} - R_{\text{comb}}^2) = 0$.
Let $a = \|\mathbf{v}\|_2^2$, $b = 2(\mathbf{r}\cdot\mathbf{v})$, $c = \|\mathbf{r}\|_2^2 - R_{\text{comb}}^2$, and discriminant $\Delta = b^2 - 4ac$:

$$\text{TTC}_k = \begin{cases} 0 & \text{if } c \le 0 \text{ (already colliding)} \\ \frac{-b - \sqrt{\Delta}}{2a} & \text{if } a > 10^{-4}, \; b < 0, \text{ and } \Delta \ge 0 \\ \infty & \text{otherwise} \end{cases}$$

#### C. Risk-Aware A* + B-Spline Safe Corridor Trajectory Planner
To navigate a robot or drone from $\mathbf{q}_{\text{start}}$ to $\mathbf{q}_{\text{goal}}$ inside the Digital Twin:
Minimize path cost over the 2D/3D grid where edge traversal cost from node $u$ to $v$ integrates Euclidean distance and the exponential risk penalty:

$$J(u \to v) = \|\mathbf{q}_v - \mathbf{q}_u\|_2 \cdot \left( 1 + \lambda_{\text{risk}} \frac{R(\mathbf{q}_u) + R(\mathbf{q}_v)}{2} + \lambda_{\text{occ}} \mathbb{I}\left(L(V_v) > L_{\text{thresh}}\right) \right)$$

Smooth the discrete A* path waypoints using cubic B-spline optimization constrained within the free-space voxel corridor.

---

### 3.10 3D Oriented Bounding Box (OBB) Virtual Geofencing
A 3D Oriented Geofence $G_m$ is defined by center $\mathbf{c}_m \in \mathbb{R}^3$, rotation matrix $\mathbf{R}_m \in SO(3)$ (yaw/pitch/roll), and half-extents $\mathbf{e}_m = [e_x, e_y, e_z]^T$. Any point or bounding-box corner $\mathbf{p}$ of a tracked entity breaches $G_m$ if and only if its local coordinates inside the geofence frame satisfy:

$$\mathbf{p}_{\text{local}} = \mathbf{R}_m^T (\mathbf{p} - \mathbf{c}_m), \qquad \text{Breach}(\mathbf{p}, G_m) \iff \bigwedge_{d \in \{x, y, z\}} \left| p_{\text{local}}^{(d)} \right| \le e_m^{(d)}$$

---

## 4. COMPLETE REPOSITORY STRUCTURE

Generate the full repository matching this exact tree:

```text
lidar_lab_digital_twin/
├── README.md
├── pyproject.toml
├── requirements.txt
├── config/
│   ├── system_config.yaml               # Master config (Sensors, Voxel Grid, ML, Tracker, Planner)
│   └── calibration_extrinsics.json      # Intrinsics K & SE(3) transforms (L2 <-> Gemini 333L <-> RPLIDAR S2)
├── core/
│   ├── __init__.py
│   ├── contracts.py                     # All Frozen Dataclasses & Enums
│   └── se3_math.py                      # SO(3)/SE(3) Lie Algebra (exp, log, hat, vee, interpolation)
├── ingestion/                           # [ENGINEER A]
│   ├── __init__.py
│   ├── lidar_l2_driver.py               # Live UDP/SDK parser (3D XYZ + Intensity + Multi-Return + IMU)
│   ├── gemini_333l_driver.py            # Orbbec Gemini 333L/335L RGB-D capture + pinhole projection
│   ├── rplidar_s2_driver.py             # Optional 2D RPLIDAR S2 serial/UDP driver + nodding servo sync
│   ├── sensor_synchronizer.py           # Software timestamp ring-buffer & cross-modal PointPainting
│   ├── recorder_replayer.py             # Multi-modal .npz high-speed stream recorder & deterministic player
│   └── synthetic_lab_simulator.py       # Physics-based 3D lab simulator (walls, benches, humans, AGVs, foliage)
├── slam_and_twin/                       # [ENGINEER A]
│   ├── __init__.py
│   ├── imu_deskew.py                    # Continuous-time IMU pre-integration & SE(3) point deskewing
│   ├── icp_pose_graph.py                # Point-to-Plane ICP odometry & Multi-way Pose-Graph optimization
│   ├── static_twin_builder.py           # TSDF / Poisson mesh reconstruction + RANSAC floor levelling
│   ├── spatial_diff_engine.py           # Range-adaptive 3D cKDTree background subtraction
│   └── log_odds_octomap.py              # 64-bit Sparse Voxel Hash + Multi-return Bayesian log-odds occupancy
├── perception_ml/                       # [ENGINEER B]
│   ├── __init__.py
│   ├── eigen_descriptors.py             # 3D DBSCAN + 10D Weinmann covariance eigenvalue invariants
│   ├── hybrid_3d_network.py             # VFE + PointNet++ Set Abstraction + Flow Correlation + Dual Heads
│   ├── point_mae_pretrain.py            # Self-supervised Masked Autoencoder for unlabeled L2 lab scans
│   └── inference_engine.py              # Batched GPU/CPU multi-task semantic & scene-flow inference wrapper
├── tracking_and_navigation/             # [ENGINEER B]
│   ├── __init__.py
│   ├── kalman_9dof_tracker.py           # 9-DoF Constant-Acceleration Kalman Filter + Mahalanobis/3D-GIoU Hungarian
│   ├── risk_and_geofence.py             # Anisotropic velocity-ellipsoid risk field, exact quadratic TTC, OBB geofence
│   ├── traffic_heatmap.py               # Persistent exponential-decay 2D/3D occupancy heatmap accumulator
│   └── safe_corridor_planner.py         # Risk-aware A* + Cubic B-Spline trajectory optimizer
├── visualization/                       # [ENGINEER A + B]
│   ├── __init__.py
│   ├── open3d_command_center.py         # Dark-mode 3D Twin Renderer (Static Mesh, Voxels, Flow Arrows, Paths)
│   ├── opencv_telemetry_hud.py          # High-contrast 2D HUD (FPS, TTC Gauges, Inferno Heatmap, Alert Log)
│   └── websocket_broadcaster.py         # Optional JSON/Binary telemetry publisher for external web dashboards
├── tests/
│   ├── test_se3_and_deskew.py
│   ├── test_voxel_hash_and_octomap.py
│   ├── test_hybrid_3d_network.py
│   └── test_kalman_and_planner.py
└── main.py                              # Unified CLI entry point (--mode sim | replay | live | build-twin | pretrain)
```

---

## 5. PRODUCTION REFERENCE IMPLEMENTATIONS

Below are the foundational reference implementations for the core mathematical and architectural modules. Expand upon these to populate the full repository.

### 5.1 `core/contracts.py` (Complete System-Wide Data Contracts)

```python
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, List, Optional, Tuple
import numpy as np

class SemanticClass(IntEnum):
    TRAVERSABLE_GROUND = 0   # Dark Slate / Risk-Colored Floor
    STATIC_STRUCTURE = 1     # Blueprint Cyan (#00B4D8)
    HUMAN_PEDESTRIAN = 2     # Neon Emerald (#00F5D4) -> Flashes Crimson (#FF0A54) on Hazard
    DYNAMIC_ROBOT_CART = 3   # Electric Violet (#9B5DE5)
    NEW_STATIC_OBSTACLE = 4  # Amber Gold (#FEE440)
    THIN_WIRE_CABLE = 5      # Laser Lime (#CCFF33)
    PERMEABLE_FOLIAGE = 6    # Coral Orange (#F15BB5) - Multi-Return Penetrable

@dataclass
class IMUSample:
    timestamp: float
    gyro: np.ndarray         # Shape: (3,) rad/s [wx, wy, wz]
    accel: np.ndarray        # Shape: (3,) m/s^2 [ax, ay, az]

@dataclass
class MultiModalSensorFrame:
    """Synchronized multi-sensor packet output by ingestion/sensor_synchronizer.py."""
    frame_id: int
    timestamp: float
    # LiDAR L2 columns: [x, y, z, intensity, return_idx, return_count, point_time_offset]
    lidar_points: np.ndarray                 # Shape: (N, 7), float32
    imu_samples: List[IMUSample] = field(default_factory=list)
    # Optional PointPainted RGB + 2D features aligned to lidar_points: [R, G, B, feat_0...feat_k]
    painted_features: Optional[np.ndarray] = None
    # Dense near-field RGB-D cloud from Gemini 333L in LiDAR frame: [x, y, z, r, g, b]
    gemini_rgbd_cloud: Optional[np.ndarray] = None
    # Optional 2D RPLIDAR S2 planar ring in LiDAR frame: [x, y, z]
    rplidar_s2_points: Optional[np.ndarray] = None
    sensor_pose_world: np.ndarray = field(default_factory=lambda: np.eye(4, dtype=np.float32))

@dataclass
class VoxelGridState:
    """Output of the 64-bit Sparse Voxel Hash & Multi-Return Log-Odds OctoMap."""
    voxel_size: float
    voxel_Play_coords: np.ndarray            # Shape: (V, 3) integer grid indices
    voxel_centers: np.ndarray                # Shape: (V, 3) metric 3D centers
    voxel_features: np.ndarray               # Shape: (V, 12) aggregated VFE features
    log_odds: np.ndarray                     # Shape: (V,) occupancy log-odds
    permeability: np.ndarray                 # Shape: (V,) multi-return penetration ratio in [0, 1]
    point_to_voxel_idx: np.ndarray           # Shape: (N_fg,) mapping foreground points to voxels

@dataclass
class DiffedLabFrame:
    """Output of the Spatial Diff Engine & Log-Odds Voxelizer."""
    frame_id: int
    timestamp: float
    dt: float
    deskewed_full_points: np.ndarray         # Shape: (N, 7)
    foreground_points: np.ndarray            # Shape: (K, 7) active dynamic/new points
    foreground_rgb: Optional[np.ndarray]     # Shape: (K, 3)
    voxel_state: VoxelGridState
    floor_grid_points: np.ndarray            # Shape: (G, 3) uniform floor coordinates
    gemini_inlay_points: Optional[np.ndarray] = None

@dataclass
class TrackedEntity3D:
    """Single 3D object tracked via 9-DoF Kalman Filter & Multi-Task Neural Heads."""
    track_id: int
    semantic_class: SemanticClass
    class_probs: np.ndarray                  # Shape: (7,) smoothed probability distribution
    confidence: float
    position: np.ndarray                     # Shape: (3,) [x, y, z]
    velocity: np.ndarray                     # Shape: (3,) [vx, vy, vz] m/s
    acceleration: np.ndarray                 # Shape: (3,) [ax, ay, az] m/s^2
    scene_flow_vector: np.ndarray            # Shape: (3,) neural flow prediction
    speed: float
    heading_rad: float
    bbox_min: np.ndarray                     # Shape: (3,)
    bbox_max: np.ndarray                     # Shape: (3,)
    obb_corners: np.ndarray                  # Shape: (8, 3) oriented bounding box vertices
    member_points: np.ndarray                # Shape: (P, 3)
    eigen_invariants: np.ndarray             # Shape: (10,) Weinmann geometric invariants
    ttc_seconds: float
    predicted_trajectory: np.ndarray         # Shape: (H, 3) future trajectory waypoints
    is_breaching_geofence: bool = False
    breached_geofence_names: List[str] = field(default_factory=list)

@dataclass
class OrientedGeofence3D:
    name: str
    center: np.ndarray                       # Shape: (3,)
    rotation_matrix: np.ndarray              # Shape: (3, 3) SO(3)
    half_extents: np.ndarray                 # Shape: (3,) [ex, ey, ez]
    severity_level: int = 2                  # 1 = Warning, 2 = Critical Breach
    is_breached: bool = False
    breaching_track_ids: List[int] = field(default_factory=list)

@dataclass
class TwinMasterFrameState:
    """Complete perception, tracking, risk, and path planning state for visualization."""
    frame_id: int
    timestamp: float
    fps: float
    sensor_pose_world: np.ndarray            # Shape: (4, 4)
    entities: List[TrackedEntity3D]
    voxel_centers: np.ndarray                # Shape: (V, 3)
    voxel_colors: np.ndarray                 # Shape: (V, 3)
    voxel_flow_vectors: np.ndarray           # Shape: (V, 3)
    floor_grid_points: np.ndarray            # Shape: (G, 3)
    floor_risk_values: np.ndarray            # Shape: (G,) in [0, 1]
    floor_risk_colors: np.ndarray            # Shape: (G, 3)
    traffic_heatmap_2d: np.ndarray           # Shape: (H_y, H_x)
    geofences: List[OrientedGeofence3D]
    planned_safe_path: Optional[np.ndarray]  # Shape: (M, 3) B-spline smoothed safe corridor path
    gemini_inlay_points: Optional[np.ndarray] = None
    active_alerts: List[str] = field(default_factory=list)
```

---

### 5.2 `core/se3_math.py` & `slam_and_twin/imu_deskew.py` (Lie Algebra $SE(3)$ & Continuous-Time IMU Deskewing)

```python
import numpy as np
from typing import List
from core.contracts import IMUSample

def skew_symmetric(v: np.ndarray) -> np.ndarray:
    """Maps a 3-vector to a 3x3 skew-symmetric matrix in so(3)."""
    return np.array([
        [0.0, -v[2], v[1]],
        [v[2], 0.0, -v[0]],
        [-v[1], v[0], 0.0]
    ], dtype=np.float64)

def so3_exp(omega: np.ndarray) -> np.ndarray:
    """Rodrigues' rotation formula mapping so(3) -> SO(3)."""
    theta = np.linalg.norm(omega)
    if theta < 1e-8:
        return np.eye(3, dtype=np.float64) + skew_symmetric(omega)
    K = skew_symmetric(omega / theta)
    return np.eye(3, dtype=np.float64) + np.sin(theta) * K + (1.0 - np.cos(theta)) * (K @ K)

def so3_log(R: np.ndarray) -> np.ndarray:
    """Logarithmic map mapping SO(3) -> so(3) vector."""
    cos_theta = np.clip((np.trace(R) - 1.0) * 0.5, -1.0, 1.0)
    theta = np.arccos(cos_theta)
    if theta < 1e-8:
        return np.zeros(3, dtype=np.float64)
    return (theta / (2.0 * np.sin(theta))) * np.array([
        R[2, 1] - R[1, 2],
        R[0, 2] - R[2, 0],
        R[1, 0] - R[0, 1]
    ], dtype=np.float64)

class ContinuousTimeIMUDeskewer:
    """
    Integrates IMU measurements across a LiDAR sweep [t0, t1] and removes
    motion distortion from individual points using interpolated SE(3) poses.
    """
    def __init__(self, gyro_bias: np.ndarray = None, accel_bias: np.ndarray = None):
        self.gyro_bias = np.zeros(3) if gyro_bias is None else gyro_bias
        self.accel_bias = np.zeros(3) if accel_bias is None else accel_bias
        self.gravity = np.array([0.0, 0.0, -9.80665], dtype=np.float64)

    def deskew_sweep(self, points: np.ndarray, imu_samples: List[IMUSample]) -> np.ndarray:
        """
        points: (N, 7) -> [x, y, z, intensity, return_idx, return_cnt, rel_time_offset]
        imu_samples: sorted list of IMUSample covering the sweep window
        Returns deskewed copy of points aligned to the sweep-end frame.
        """
        if len(imu_samples) < 2 or len(points) == 0:
            return points.copy()

        # Integrate IMU trajectory states (t_k, R_k, v_k, p_k)
        times = [imu_samples[0].timestamp]
        R_list = [np.eye(3, dtype=np.float64)]
        v_list = [np.zeros(3, dtype=np.float64)]
        p_list = [np.zeros(3, dtype=np.float64)]

        for k in range(len(imu_samples) - 1):
            dt = max(1e-5, imu_samples[k + 1].timestamp - imu_samples[k].timestamp)
            omega = imu_samples[k].gyro.astype(np.float64) - self.gyro_bias
            accel = imu_samples[k].accel.astype(np.float64) - self.accel_bias

            R_prev, v_prev, p_prev = R_list[-1], v_list[-1], p_list[-1]
            acc_world = R_prev @ accel + self.gravity

            R_next = R_prev @ so3_exp(omega * dt)
            v_next = v_prev + acc_world * dt
            p_next = p_prev + v_prev * dt + 0.5 * acc_world * (dt ** 2)

            times.append(imu_samples[k + 1].timestamp)
            R_list.append(R_next)
            v_list.append(v_next)
            p_list.append(p_next)

        times = np.array(times)
        t_start, t_end = times[0], times[-1]
        sweep_duration = max(1e-4, t_end - t_start)

        R_end_inv = R_list[-1].T
        p_end = p_list[-1]

        # Quantize point relative offsets into 32 temporal bins for vectorized SE(3) deskewing
        rel_offsets = np.clip(points[:, 6], 0.0, sweep_duration)
        point_abs_times = t_start + rel_offsets

        num_bins = 32
        bin_edges = np.linspace(t_start, t_end, num_bins + 1)
        bin_indices = np.clip(np.digitize(point_abs_times, bin_edges) - 1, 0, num_bins - 1)

        deskewed = points.copy()
        xyz = points[:, :3].astype(np.float64)

        for b in range(num_bins):
            mask = (bin_indices == b)
            if not np.any(mask):
                continue
            t_mid = 0.5 * (bin_edges[b] + bin_edges[b + 1])
            idx = np.clip(np.searchsorted(times, t_mid) - 1, 0, len(times) - 2)
            alpha = np.clip((t_mid - times[idx]) / max(1e-6, times[idx + 1] - times[idx]), 0.0, 1.0)

            # Geodesic SO(3) interpolation and linear translation interpolation
            dR = so3_log(R_list[idx].T @ R_list[idx + 1])
            R_interp = R_list[idx] @ so3_exp(alpha * dR)
            p_interp = (1.0 - alpha) * p_list[idx] + alpha * p_list[idx + 1]

            # Transform from sensor pose at t_mid to sweep-end pose at t_end
            R_rel = R_end_inv @ R_interp
            t_rel = R_end_inv @ (p_interp - p_end)
            deskewed[mask, :3] = (xyz[mask] @ R_rel.T + t_rel).astype(np.float32)

        return deskewed
```

---

### 5.3 `slam_and_twin/log_odds_octomap.py` (64-Bit Sparse Voxel Hash & Multi-Return Bayesian Occupancy)

```python
import numpy as np
from core.contracts import VoxelGridState

class SparseMultiReturnVoxelMap:
    """
    Maintains a 64-bit spatial hash voxel grid with Voxel Feature Encoding (VFE)
    and Bayesian Log-Odds updates incorporating multi-return LiDAR beam penetration.
    """
    PRIME_X = np.int64(73856093)
    PRIME_Y = np.int64(19349663)
    PRIME_Z = np.int64(83492791)

    def __init__(self, voxel_size: float = 0.10, decay_rate: float = 0.96):
        self.voxel_size = voxel_size
        self.decay_rate = decay_rate
        self.log_odds_memory = {}      # hash_key -> float
        self.permeability_memory = {}  # hash_key -> float

    @classmethod
    def compute_spatial_hash(cls, voxel_coords: np.ndarray) -> np.ndarray:
        """voxel_coords: (N, 3) int64 -> returns (N,) int64 hash keys."""
        vx = voxel_coords[:, 0].astype(np.int64)
        vy = voxel_coords[:, 1].astype(np.int64)
        vz = voxel_coords[:, 2].astype(np.int64)
        return (vx * cls.PRIME_X) ^ (vy * cls.PRIME_Y) ^ (vz * cls.PRIME_Z)

    def voxelize_and_update(
        self, points: np.ndarray, rgb: np.ndarray = None
    ) -> VoxelGridState:
        """
        points: (N, 7) -> [x, y, z, intensity, return_idx, return_count, t_offset]
        rgb: optional (N, 3) float32 in [0, 1] from Gemini 333L PointPainting
        """
        if len(points) == 0:
            return VoxelGridState(
                voxel_size=self.voxel_size,
                voxel_Play_coords=np.empty((0, 3), dtype=np.int32),
                voxel_centers=np.empty((0, 3), dtype=np.float32),
                voxel_features=np.empty((0, 12), dtype=np.float32),
                log_odds=np.empty((0,), dtype=np.float32),
                permeability=np.empty((0,), dtype=np.float32),
                point_to_voxel_idx=np.empty((0,), dtype=np.int64),
            )

        xyz = points[:, :3]
        intensity = points[:, 3]
        return_idx = points[:, 4]
        return_cnt = np.maximum(points[:, 5], 1.0)

        # Penetration indicator: >0 when pulse had subsequent returns behind this point
        penetration_score = (return_cnt - return_idx) / return_cnt

        voxel_coords = np.floor(xyz / self.voxel_size).astype(np.int32)
        hashes = self.compute_spatial_hash(voxel_coords)

        unique_hashes, unique_first_idx, inverse_idx, counts = np.unique(
            hashes, return_index=True, return_inverse=True, return_counts=True
        )
        num_voxels = len(unique_hashes)
        unique_coords = voxel_coords[unique_first_idx]

        # Vectorized Voxel Feature Aggregation via np.add.at
        sum_xyz = np.zeros((num_voxels, 3), dtype=np.float64)
        sum_int = np.zeros(num_voxels, dtype=np.float64)
        sum_pen = np.zeros(num_voxels, dtype=np.float64)
        min_z = np.full(num_voxels, np.inf, dtype=np.float32)
        max_z = np.full(num_voxels, -np.inf, dtype=np.float32)

        np.add.at(sum_xyz, inverse_idx, xyz)
        np.add.at(sum_int, inverse_idx, intensity)
        np.add.at(sum_pen, inverse_idx, penetration_score)
        np.minimum.at(min_z, inverse_idx, xyz[:, 2])
        np.maximum.at(max_z, inverse_idx, xyz[:, 2])

        counts_f = counts.astype(np.float32)
        mean_xyz = (sum_xyz / counts[:, None]).astype(np.float32)
        mean_int = (sum_int / counts_f).astype(np.float32)
        mean_pen = (sum_pen / counts_f).astype(np.float32)
        height_span = np.maximum(0.0, max_z - min_z)
        log_density = np.log1p(counts_f)

        if rgb is not None and len(rgb) == len(points):
            sum_rgb = np.zeros((num_voxels, 3), dtype=np.float64)
            np.add.at(sum_rgb, inverse_idx, rgb)
            mean_rgb = (sum_rgb / counts[:, None]).astype(np.float32)
        else:
            mean_rgb = np.full((num_voxels, 3), 0.5, dtype=np.float32)

        # Update Bayesian Log-Odds per active voxel
        log_odds_arr = np.zeros(num_voxels, dtype=np.float32)
        perm_arr = np.zeros(num_voxels, dtype=np.float32)

        for v_i, h_key in enumerate(unique_hashes):
            prev_lo = self.log_odds_memory.get(int(h_key), 0.0) * self.decay_rate
            # Solid return vs permeable multi-return update
            delta_lo = 0.25 if mean_pen[v_i] > 0.35 else 0.85
            new_lo = float(np.clip(prev_lo + delta_lo, -4.0, 4.0))
            self.log_odds_memory[int(h_key)] = new_lo
            log_odds_arr[v_i] = new_lo

            prev_perm = self.permeability_memory.get(int(h_key), mean_pen[v_i])
            new_perm = float(0.7 * prev_perm + 0.3 * mean_pen[v_i])
            self.permeability_memory[int(h_key)] = new_perm
            perm_arr[v_i] = new_perm

        # 12D Voxel Feature Vector: [cx, cy, cz, mean_int, height_span, log_density, perm, log_odds, r, g, b, z_max]
        voxel_features = np.column_stack([
            mean_xyz,
            mean_int,
            height_span,
            log_density,
            perm_arr,
            log_odds_arr,
            mean_rgb,
            max_z,
        ]).astype(np.float32)

        return VoxelGridState(
            voxel_size=self.voxel_size,
            voxel_Play_coords=unique_coords,
            voxel_centers=mean_xyz,
            voxel_features=voxel_features,
            log_odds=log_odds_arr,
            permeability=perm_arr,
            point_to_voxel_idx=inverse_idx,
        )
```

---

### 5.4 `perception_ml/hybrid_3d_network.py` (Multi-Task 3D Sparse Voxel Attention + Scene Flow Network)

```python
import torch
import torch.nn as nn
import torch.nn.functional as F

class SparseVoxelKNNLayer(nn.Module):
    """
    Local K-NN Graph Attention over active 3D voxel centroids and geometric invariants.
    Processes arbitrary numbers of sparse 3D voxels in real time without dense 3D grids.
    """
    def __init__(self, in_channels: int, out_channels: int, k_neighbors: int = 16):
        super().__init__()
        self.k = k_neighbors
        self.pos_mlp = nn.Sequential(
            nn.Linear(3, in_channels),
            nn.GELU(),
            nn.Linear(in_channels, in_channels),
        )
        self.edge_mlp = nn.Sequential(
            nn.Linear(in_channels * 2 + 3, out_channels),
            nn.LayerNorm(out_channels),
            nn.GELU(),
            nn.Linear(out_channels, out_channels),
            nn.LayerNorm(out_channels),
            nn.GELU(),
        )
        self.shortcut = nn.Linear(in_channels, out_channels) if in_channels != out_channels else nn.Identity()

    def forward(self, coords: torch.Tensor, features: torch.Tensor) -> torch.Tensor:
        """
        coords: (V, 3) metric voxel centers
        features: (V, C) voxel feature embeddings
        """
        V = coords.shape[0]
        k_actual = min(self.k, V)
        if k_actual <= 1:
            return self.shortcut(features)

        # Compute pairwise Euclidean distances between active voxels
        dist_mat = torch.cdist(coords.unsqueeze(0), coords.unsqueeze(0)).squeeze(0)  # (V, V)
        _, knn_idx = torch.topk(dist_mat, k=k_actual, largest=False, dim=-1)         # (V, k)

        neighbor_coords = coords[knn_idx]                                            # (V, k, 3)
        rel_pos = neighbor_coords - coords.unsqueeze(1)                              # (V, k, 3)
        pos_enc = self.pos_mlp(rel_pos)                                              # (V, k, C)

        neighbor_feats = features[knn_idx] + pos_enc                                 # (V, k, C)
        center_feats = features.unsqueeze(1).expand(-1, k_actual, -1)                # (V, k, C)

        edge_input = torch.cat([center_feats, neighbor_feats - center_feats, rel_pos], dim=-1)
        edge_feats = self.edge_mlp(edge_input)                                       # (V, k, out_C)
        pooled_feats = torch.max(edge_feats, dim=1)[0]                               # (V, out_C)

        return pooled_feats + self.shortcut(features)

class CrossFrameFlowCorrelation(nn.Module):
    """
    Computes 3D Temporal Cost-Volume Attention between active voxels at frame t
    and active voxels at frame t-1 to predict 3D Scene Flow vectors (dx, dy, dz).
    """
    def __init__(self, feat_dim: int, k_flow: int = 12):
        super().__init__()
        self.k_flow = k_flow
        self.q_proj = nn.Linear(feat_dim, feat_dim)
        self.k_proj = nn.Linear(feat_dim, feat_dim)
        self.flow_mlp = nn.Sequential(
            nn.Linear(feat_dim + 3, feat_dim),
            nn.LayerNorm(feat_dim),
            nn.GELU(),
            nn.Linear(feat_dim, feat_dim),
        )

    def forward(
        self,
        coords_t: torch.Tensor,
        feats_t: torch.Tensor,
        coords_prev: torch.Tensor,
        feats_prev: torch.Tensor,
    ) -> torch.Tensor:
        if coords_prev is None or coords_prev.shape[0] == 0:
            return feats_t

        k_actual = min(self.k_flow, coords_prev.shape[0])
        cross_dist = torch.cdist(coords_t.unsqueeze(0), coords_prev.unsqueeze(0)).squeeze(0)
        _, knn_idx = torch.topk(cross_dist, k=k_actual, largest=False, dim=-1)       # (V_t, k)

        disp = coords_t.unsqueeze(1) - coords_prev[knn_idx]                          # (V_t, k, 3)
        prev_knn_feats = feats_prev[knn_idx]                                         # (V_t, k, D)

        q = self.q_proj(feats_t).unsqueeze(1)                                        # (V_t, 1, D)
        k = self.k_proj(prev_knn_feats)                                              # (V_t, k, D)
        attn = F.softmax(torch.sum(q * k, dim=-1) / (feats_t.shape[-1] ** 0.5), dim=-1)  # (V_t, k)

        flow_msg = self.flow_mlp(torch.cat([prev_knn_feats, disp], dim=-1))          # (V_t, k, D)
        aggregated_flow_feat = torch.sum(attn.unsqueeze(-1) * flow_msg, dim=1)       # (V_t, D)

        return feats_t + aggregated_flow_feat

class MultiTaskVoxelSceneFlowNet(nn.Module):
    """
    Complete Multi-Task 3D Voxel Network:
    - Input: 12D Voxel Features + 10D Weinmann Covariance Eigen-Descriptors
    - Head A: 7-Class 3D Semantic Segmentation
    - Head B: 3D Scene Flow Displacement Vector (dx, dy, dz)
    """
    def __init__(self, voxel_in_dim: int = 12, eigen_in_dim: int = 10, num_classes: int = 7):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Linear(voxel_in_dim + eigen_in_dim, 64),
            nn.LayerNorm(64),
            nn.GELU(),
        )
        self.stage1 = SparseVoxelKNNLayer(64, 128, k_neighbors=16)
        self.stage2 = SparseVoxelKNNLayer(128, 256, k_neighbors=16)
        self.flow_corr = CrossFrameFlowCorrelation(feat_dim=256, k_flow=12)

        self.semantic_head = nn.Sequential(
            nn.Linear(256 + 128, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Linear(128, num_classes),
        )
        self.scene_flow_head = nn.Sequential(
            nn.Linear(256 + 128, 128),
            nn.LayerNorm(128),
            nn.GELU(),
            nn.Linear(128, 3),  # Predicts (dx, dy, dz) meters per frame
        )

    def forward(
        self,
        coords_t: torch.Tensor,
        voxel_feats_t: torch.Tensor,
        eigen_feats_t: torch.Tensor,
        coords_prev: torch.Tensor = None,
        cached_stage2_prev: torch.Tensor = None,
    ):
        x0 = self.stem(torch.cat([voxel_feats_t, eigen_feats_t], dim=-1))
        x1 = self.stage1(coords_t, x0)
        x2 = self.stage2(coords_t, x1)

        x_temporal = self.flow_corr(coords_t, x2, coords_prev, cached_stage2_prev)
        multi_scale = torch.cat([x_temporal, x1], dim=-1)

        sem_logits = self.semantic_head(multi_scale)
        flow_vectors = self.scene_flow_head(multi_scale)
        return sem_logits, flow_vectors, x2.detach()
```

---

### 5.5 `tracking_and_navigation/risk_and_geofence.py` & `safe_corridor_planner.py` (Anisotropic Risk Field, Exact TTC, 3D OBB Geofence & Risk-Aware A* Planner)

```python
import heapq
import numpy as np
from scipy.interpolate import splprep, splev
from typing import List, Optional, Tuple
from core.contracts import TrackedEntity3D, OrientedGeofence3D

class RiskGeofenceAndPlannerEngine:
    def __init__(
        self,
        sigma_base: float = 0.65,
        alpha_front: float = 0.75,
        alpha_lat: float = 0.20,
        protected_origin: np.ndarray = None,
        safety_radius: float = 0.55,
    ):
        self.sigma_base = sigma_base
        self.alpha_front = alpha_front
        self.alpha_lat = alpha_lat
        self.protected_origin = np.zeros(3, dtype=np.float32) if protected_origin is None else protected_origin
        self.safety_radius = safety_radius

    def compute_exact_ttc(self, pos: np.ndarray, vel: np.ndarray, entity_radius: float = 0.35) -> float:
        """Solves exact quadratic ray-sphere intersection for Time-To-Collision."""
        r = pos - self.protected_origin
        v = vel
        R_comb = self.safety_radius + entity_radius

        c = float(np.dot(r, r) - R_comb ** 2)
        if c <= 0.0:
            return 0.0

        a = float(np.dot(v, v))
        if a < 1e-4:
            return float("inf")

        b = float(2.0 * np.dot(r, v))
        if b >= 0.0:
            return float("inf")  # Moving away

        disc = b * b - 4.0 * a * c
        if disc < 0.0:
            return float("inf")

        ttc = (-b - np.sqrt(disc)) / (2.0 * a)
        return float(ttc) if ttc >= 0.0 else float("inf")

    def evaluate_3d_obb_geofences(
        self, entities: List[TrackedEntity3D], geofences: List[OrientedGeofence3D]
    ) -> List[str]:
        """Checks 8-corner OBB + centroid inclusion inside 3D Oriented Geofences."""
        alerts = []
        for gf in geofences:
            gf.is_breached = False
            gf.breaching_track_ids.clear()

        for ent in entities:
            ent.is_breaching_geofence = False
            ent.breached_geofence_names.clear()
            test_pts = np.vstack([ent.centroid[None, :], ent.obb_corners])  # (9, 3)

            for gf in geofences:
                local_pts = (test_pts - gf.center) @ gf.rotation_matrix     # R^T (p - c)
                inside_mask = np.all(np.abs(local_pts) <= gf.half_extents[None, :], axis=1)
                if np.any(inside_mask):
                    ent.is_breaching_geofence = True
                    ent.breached_geofence_names.append(gf.name)
                    gf.is_breached = True
                    gf.breaching_track_ids.append(ent.track_id)
                    alerts.append(
                        f"[BREACH] Track #{ent.track_id} ({ent.semantic_class.name}) entered {gf.name}! "
                        f"Speed={ent.speed:.2f}m/s, TTC={ent.ttc_seconds:.1f}s"
                    )
        return alerts

    def compute_anisotropic_floor_risk(
        self, floor_points: np.ndarray, entities: List[TrackedEntity3D]
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Computes directional velocity-stretched risk potential R(q) in [0, 1]
        and corresponding RGB floor color map.
        """
        G = len(floor_points)
        if G == 0 or len(entities) == 0:
            colors = np.tile(np.array([0.05, 0.45, 0.25], dtype=np.float32), (G, 1))
            return np.zeros(G, dtype=np.float32), colors

        q_xy = floor_points[:, :2]
        survival_prob = np.ones(G, dtype=np.float32)

        for ent in entities:
            p_xy = ent.position[:2]
            v_xy = ent.velocity[:2]
            speed = float(np.linalg.norm(v_xy))
            psi = float(np.arctan2(v_xy[1], v_xy[0])) if speed > 0.1 else 0.0

            cos_p, sin_p = np.cos(psi), np.sin(psi)
            d = q_xy - p_xy
            d_par = cos_p * d[:, 0] + sin_p * d[:, 1]
            d_perp = -sin_p * d[:, 0] + cos_p * d[:, 1]

            sigma_par = np.where(
                d_par >= 0.0,
                self.sigma_base + self.alpha_front * speed,
                self.sigma_base,
            )
            sigma_perp = self.sigma_base + self.alpha_lat * speed

            r_k = np.exp(-0.5 * ((d_par / sigma_par) ** 2 + (d_perp / sigma_perp) ** 2))
            survival_prob *= (1.0 - r_k)

        risk = 1.0 - survival_prob
        colors = np.zeros((G, 3), dtype=np.float32)
        colors[:, 0] = np.clip(1.4 * risk, 0.05, 1.0)               # Red rises with risk
        colors[:, 1] = np.clip(0.75 * (1.0 - 0.8 * risk), 0.05, 1.0) # Green in safe zones
        colors[:, 2] = np.clip(0.25 * (1.0 - risk), 0.05, 0.4)
        return risk, colors

    def plan_safe_corridor_path(
        self,
        start_xy: np.ndarray,
        goal_xy: np.ndarray,
        floor_points: np.ndarray,
        floor_risk: np.ndarray,
        grid_shape: Tuple[int, int],
        grid_res: float = 0.25,
        origin_xy: Tuple[float, float] = (-8.0, -8.0),
    ) -> Optional[np.ndarray]:
        """
        Runs Risk-Aware 8-Connected A* over the floor costmap followed by Cubic B-Spline smoothing.
        """
        ny, nx = grid_shape
        risk_grid = floor_risk.reshape((ny, nx))

        def to_idx(pt):
            ix = int(np.clip((pt[0] - origin_xy[0]) / grid_res, 0, nx - 1))
            iy = int(np.clip((pt[1] - origin_xy[1]) / grid_res, 0, ny - 1))
            return (iy, ix)

        def to_xy(iy, ix):
            return np.array([origin_xy[0] + ix * grid_res, origin_xy[1] + iy * grid_res, 0.08], dtype=np.float32)

        s_node, g_node = to_idx(start_xy), to_idx(goal_xy)
        open_heap = [(0.0, s_node)]
        g_score = {s_node: 0.0}
        came_from = {}

        neighbors = [(-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)]
        while open_heap:
            _, curr = heapq.heappop(open_heap)
            if curr == g_node:
                break
            for dy, dx in neighbors:
                ny_i, nx_i = curr[0] + dy, curr[1] + dx
                if not (0 <= ny_i < ny and 0 <= nx_i < nx):
                    continue
                r_val = float(risk_grid[ny_i, nx_i])
                if r_val > 0.88:
                    continue  # Hard collision barrier
                step_dist = np.hypot(dy, dx) * grid_res
                edge_cost = step_dist * (1.0 + 12.0 * (r_val ** 2))
                tentative_g = g_score[curr] + edge_cost
                nxt = (ny_i, nx_i)
                if tentative_g < g_score.get(nxt, float("inf")):
                    came_from[nxt] = curr
                    g_score[nxt] = tentative_g
                    h = np.hypot(g_node[0] - ny_i, g_node[1] - nx_i) * grid_res
                    heapq.heappush(open_heap, (tentative_g + h, nxt))

        if g_node not in came_from and g_node != s_node:
            return None

        # Reconstruct path
        waypoints = [to_xy(*g_node)]
        curr = g_node
        while curr in came_from:
            curr = came_from[curr]
            waypoints.append(to_xy(*curr))
        waypoints.reverse()
        waypoints_arr = np.array(waypoints, dtype=np.float32)

        if len(waypoints_arr) >= 4:
            try:
                tck, _ = splprep([waypoints_arr[:, 0], waypoints_arr[:, 1]], s=0.15, k=3)
                u_fine = np.linspace(0, 1, 60)
                x_s, y_s = splev(u_fine, tck)
                return np.column_stack([x_s, y_s, np.full_like(x_s, 0.08)]).astype(np.float32)
            except Exception:
                return waypoints_arr
        return waypoints_arr
```

---

## 6. TWO-ENGINEER PARALLEL ARCHITECTURE & INTEGRATION ROADMAP

Structure the system implementation across two parallel engineering tracks (**Engineer A** and **Engineer B**). Both engineers share `core/contracts.py` and `core/se3_math.py` as their immutable interface boundary.

| Phase | Engineer A: Systems, Multi-Sensor Fusion, SLAM & 3D Engine | Engineer B: 3D Deep Learning, Scene Flow, Kalman Tracking & Planning | Joint Integration Verification |
| :--- | :--- | :--- | :--- |
| **Phase 1: Core Math, Contracts & Multi-Sensor Simulation** | Implement `core/contracts.py`, `core/se3_math.py`, and `ingestion/synthetic_lab_simulator.py` (simulating a complete 3D lab with benches, pillars, walking humans, moving AGV carts, suspended cables, potted foliage with simulated multi-return echoes, IMU streams, and synthetic Gemini 333L RGB-D frustums). | Implement `perception_ml/eigen_descriptors.py` (vectorized 3D DBSCAN + 10D Weinmann covariance invariants) and `tracking_and_navigation/kalman_9dof_tracker.py` (9-DoF Constant-Acceleration Kalman Filter + Mahalanobis & 3D-GIoU Hungarian matcher). | Verify `SyntheticLabSimulator` feeds `MultiModalSensorFrame` objects directly into Engineer B's clustering and 9-DoF tracker with zero shape mismatches. |
| **Phase 2: Live Hardware Drivers, IMU Deskewing & Multi-Task 3D Neural Network** | Implement `lidar_l2_driver.py`, `gemini_333l_driver.py`, `rplidar_s2_driver.py`, `sensor_synchronizer.py` (cross-modal PointPainting), and `imu_deskew.py` (continuous-time $SE(3)$ motion undistortion). | Implement `hybrid_3d_network.py` (`MultiTaskVoxelSceneFlowNet` with `SparseVoxelKNNLayer` + `CrossFrameFlowCorrelation`), `point_mae_pretrain.py` (Chamfer-loss masked autoencoder), and `inference_engine.py`. | Run end-to-end forward/backward passes of `MultiTaskVoxelSceneFlowNet` on deskewed and PointPainted voxel batches. |
| **Phase 3: Static Digital Twin Reconstruction & Probabilistic OctoMap** | Implement `icp_pose_graph.py` (Point-to-Plane ICP + multi-way Pose-Graph optimization), `static_twin_builder.py` (TSDF/Poisson surface mesh + RANSAC floor alignment), `spatial_diff_engine.py`, and `log_odds_octomap.py` (64-bit hash + multi-return permeability). | Implement `risk_and_geofence.py` (anisotropic velocity-ellipsoid risk field, exact quadratic TTC, 3D OBB geofencing), `traffic_heatmap.py`, and `safe_corridor_planner.py` (Risk-Aware A* + B-Spline corridor smoother). | Verify foreground extraction strips static walls cleanly while preserving newly placed static boxes, walking humans, and multi-return permeable objects. |
| **Phase 4: Command Center 3D Visualization, Telemetry HUD & Full System Polish** | Implement `open3d_command_center.py` (non-blocking dark-mode 3D renderer showing the static lab mesh, dynamic semantic voxels, 3D scene-flow arrows, OBB geofence cages, Gemini RGB-D inlay, and green B-Spline navigation paths). | Implement `opencv_telemetry_hud.py` (2D tactical overlay with live FPS, per-track TTC gauges, 2D Inferno traffic heatmap, and breach logs), `websocket_broadcaster.py`, and the complete `tests/` verification suite. | Execute full multi-modal pipeline across `--mode sim`, `--mode replay`, `--mode pretrain`, `--mode build-twin`, and `--mode live`. |

---

## 7. AGENT EXECUTION INSTRUCTIONS (HOW TO GENERATE THE CODEBASE)

When executing this prompt, adhere strictly to the following generation rules:
1. **Completeness Over Brevity:** Write out every file in full. Never use placeholders like `# TODO: implement math here` or `pass`. Every mathematical equation specified in Section 3 must appear in code.
2. **Self-Contained Zero-Hardware Bootstrapping:** Ensure that running `python main.py --mode sim` immediately launches the physics-based 3D Lab Simulator, runs IMU deskewing, PointPainting, KD-Tree background diffing, 64-bit log-odds voxelization, `MultiTaskVoxelSceneFlowNet` inference, 9-DoF Kalman tracking, OBB geofence monitoring, Risk-Aware A* path planning, and renders both the Open3D 3D Command Center and OpenCV Telemetry HUD out-of-the-box.
3. **Fault-Tolerant Hardware Drivers:** Wrap physical SDK/socket imports (`pyserial`, Orbbec SDK, UDP sockets) in clean try/except adapters inside `ingestion/` so the codebase runs on any machine while seamlessly switching to live hardware when `--mode live` is passed.
4. **Systematic File Generation Order:**
   * **Step 1:** Generate `requirements.txt`, `pyproject.toml`, `config/system_config.yaml`, and `config/calibration_extrinsics.json`.
   * **Step 2:** Generate `core/contracts.py` and `core/se3_math.py`.
   * **Step 3:** Generate all modules in `ingestion/` (`synthetic_lab_simulator.py`, `sensor_synchronizer.py`, `lidar_l2_driver.py`, `gemini_333l_driver.py`, `rplidar_s2_driver.py`, `recorder_replayer.py`).
   * **Step 4:** Generate all modules in `slam_and_twin/` (`imu_deskew.py`, `icp_pose_graph.py`, `static_twin_builder.py`, `spatial_diff_engine.py`, `log_odds_octomap.py`).
   * **Step 5:** Generate all modules in `perception_ml/` (`eigen_descriptors.py`, `hybrid_3d_network.py`, `point_mae_pretrain.py`, `inference_engine.py`).
   * **Step 6:** Generate all modules in `tracking_and_navigation/` (`kalman_9dof_tracker.py`, `risk_and_geofence.py`, `traffic_heatmap.py`, `safe_corridor_planner.py`).
   * **Step 7:** Generate all modules in `visualization/` (`open3d_command_center.py`, `opencv_telemetry_hud.py`, `websocket_broadcaster.py`), `main.py`, and the unit tests in `tests/`.
