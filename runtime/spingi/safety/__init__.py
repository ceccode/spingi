"""Safety layer S2 (runtime-spec, section 5): an independent monitor that can stop the robot."""

from spingi.safety.limits import Geofence, SafetyLimits
from spingi.safety.monitor import SafetyMonitor

__all__ = ["Geofence", "SafetyLimits", "SafetyMonitor"]
