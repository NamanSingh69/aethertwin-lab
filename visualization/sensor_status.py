"""Presentation status from explicit immutable snapshot diagnostics only."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class SensorStatus:
    mode: str
    issues: tuple[str,...] = ()
    details: tuple[str,...] = ()

    @property
    def degraded(self):
        return bool(self.issues)

    @property
    def summary(self):
        return " | ".join((self.mode,*self.issues))


def sensor_status(diagnostics):
    """Do not infer health or acquisition mode from absent point/image arrays.

    Replay preserves recorded sensor metadata; source_mode describes the current
    acquisition path and therefore takes precedence over recorded synthetic or
    transport fields. Unknown source metadata remains visibly unreported.
    Error details are retained in full for the command-center inspector.
    """
    mode={"live":"Live","replay":"Replay","sim":"Sim"}.get(diagnostics.get("source_mode"))
    if mode is None:
        mode="Sim" if diagnostics.get("synthetic") is True else "Source not reported"
    issues=[]; details=[]
    s2_error=diagnostics.get("s2_error")
    if diagnostics.get("s2_degraded") is True:
        issues.append("Optional S2 degraded")
    elif s2_error:
        issues.append("Optional S2 reported error")
    if s2_error: details.append("S2: "+str(s2_error))
    camera_unavailable=diagnostics.get("camera_available") is False
    fusion_unavailable=diagnostics.get("painting")=="camera_unavailable"
    camera_error=diagnostics.get("camera_error") or diagnostics.get("acquisition_error")
    if camera_unavailable or fusion_unavailable:
        issues.append("RGB-D unavailable")
        if not camera_error:
            details.append("RGB-D: camera driver reports no available frame." if camera_unavailable
                else "RGB-D: camera data or calibration unavailable for fusion.")
    elif camera_error:
        issues.append("RGB-D reported error")
    if camera_error: details.append("RGB-D: "+str(camera_error))
    if mode=="Live" and diagnostics.get("synthetic") is True:
        issues.append("Synthetic data reported")
        details.append("Live mode reports synthetic observations; verify the acquisition source.")
    return SensorStatus(mode,tuple(issues),tuple(details))
