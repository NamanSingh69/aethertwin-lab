# Rig tape measurements (LAB_DAY step 1), 2026-11-05

Measured by the team with a Stanley 5 m tape. Photos: `photos/step1_ruler_1.jpg` .. `_6.jpg`. All heights above
the surface the cart stands on. Status: **team** = measured and sent by the team; **derived** = arithmetic on team
values; **photo** = read off the photos by the agent (an estimate). Tape resolution about 0.5 cm.

| Quantity | Value | Status | Note |
|---|---|---|---|
| L2 window centre above floor (`--lidar-height`) | 9.0 cm | team | team also sent 8.5 cm for the L2 top first; the 9.0 cm window value is used |
| Camera lens centre above floor | 11.5 cm | team | |
| Camera lens relative to the L2 centre | 21 cm forward, 0 cm left, 2.5 cm up | team (up derived) | sideways not sent; 0 from photo 6 |
| Outer footprint, whole cart (wheels, L2, camera) | 9 in x 6 in = 22.9 x 15.2 cm | team | length (forward) x width |
| Outer height (top of camera) | ~13.5 cm | photo | photo 4 |
| L2 centre relative to footprint centre | ~ -7.5 cm forward, 0 cm left | photo + derived | L2 at the rear (base ~8 cm across); 21 cm to the camera plus the camera bar exceeds 22.9 cm by ~2-3 cm, inside tape and estimate error |
| Plate only | 8.5 in x 4 in (21.6 x 10.2 cm) | team | |
| Mount | L2, camera and Pi fixed firmly to the cart | team | |

Note: the camera (lens 2.5 cm above the L2 window, top ~4.5 cm above it, 21 cm in front) is above the L2's horizon,
so the L2 sees the camera bar: the self-mask (step 2) is expected to suggest a box for it.
