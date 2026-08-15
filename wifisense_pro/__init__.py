"""Compatibility namespace for users upgrading from WiFiSense Pro."""
from wifisense_ultra import __version__
from wifisense_ultra.ultra_engine import UltraEngine as DetectionEngine

__all__ = ["DetectionEngine", "__version__"]
