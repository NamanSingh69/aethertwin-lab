# AetherTwin addendum review

The new document is preserved as `PEAK_HIL_AND_UI_SPEC.md`, with its complete
original physical blueprint in `references/AetherTwin_addendum_original.txt`.
It proposes a material scope extension: hardware diagnostics, a telemetry halo,
and a browser command center alongside the native viewer. The user selected
review/revision first, confirmed no sensors are connected, and set 35 ms as a
future target rather than a completion gate. The revised plan is in
`AETHERTWIN_REVISED_PLAN.md`; extension implementation has not started.

The following corrections preserve the requested capabilities while keeping
verification truthful and consistent with the original approved platform:

1. **Keep hardware and simulation results distinct.** A simulated fallback may
   exercise HIL logic when explicitly selected/reported, but it cannot make a
   missing physical device pass. Required hardware stages should report passed,
   failed or not tested, with overall hardware success false until measured.
   Live acquisition must continue to reject silent simulation substitution.
2. **Respect device capabilities.** Unitree L2 does not supply echo index/count.
   Multi-return processing can be verified synthetically or through compatible
   recordings; it cannot be claimed as physically verified on an L2 packet.
3. **Report calibration observability.** A single floor plane constrains two
   relative tilt angles and translation normal to the plane. It does not recover
   yaw or the two translations within the plane. Preserve and identify the
   remaining measured/CAD priors, report rank/uncertainty, and require additional
   nonparallel geometry or measured correspondences for a full extrinsic claim.
4. **Measure performance honestly.** The current full CPU pipeline is around
   1.3 seconds per frame, while the presentation loop runs independently. A
   35 ms end-to-end criterion and 60 FPS browser rendering are different targets.
   Neither should be marked passed from example numbers or reduced sampling
   hidden from the report.
5. **Use actual hardware discovery.** A UDP bind, an arbitrary packet, a USB
   vendor ID or a `/dev/ttyUSB0` pathname is not proof of a valid sensor stream.
   Probe configured devices through their SDK/protocol, validate timestamps,
   packet identity and coverage, and report firmware/profile/transport evidence.
6. **Keep operating-system changes reviewable.** The pasted network, firewall,
   group-membership and world-writable USB rules are example setup instructions.
   They have not been executed. Match any future setup to the actual interface,
   supported device and required permissions. Ubuntu 24.04 remains the original
   live integration target unless the user changes it.
7. **Treat code generation and device flashing separately.** Firmware source,
   virtual halo states and host protocol tests can be delivered without flashing
   a microcontroller. Physical outputs require the intended identified port and
   device; no robot control or automatic firmware changes are implied.
8. **Describe binary streaming precisely.** Contiguous binary sections and
   JavaScript typed-array views avoid large JSON arrays. GPU upload and any
   instance-matrix construction still involve work/copies; do not claim literal
   end-to-end zero-copy without measuring and proving it.
9. **Adapt reference calls to the real contracts.** The pasted tester uses
   simulator, tracker and planner methods/fields that differ from the implemented
   platform. Its unconditional `all_passed=True` is not an acceptance test.
10. **Separate integrity from identity signatures.** A report hash detects
    changes; a cryptographic signature additionally needs a signing key and a
    stated trust model. Label either result accurately.

These notes are a review of the requested design, not a claim that physical
hardware has been connected, calibrated or tested.
