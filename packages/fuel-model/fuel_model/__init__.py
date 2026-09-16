"""Multi-vessel fuel model — PCHIP interpolation, derived L/nm, bidirectional mapping."""

from .curve import AmbiguousInversionError, FuelCurve
from .interpolation import pchip_interpolate, monotonic_cubic_interp
from .vessel import (
    VesselProfile,
    Axopar28V1,
    Axopar25CrossTop,
    Axopar22TTop,
    Dusky233Evinrude300,
    RobaloR180,
    RobaloR160,
    BostonWhaler210Dauntless,
    BostonWhaler190Montauk,
    GradyWhiteFisherman236,
)
from .catalog import VESSEL_CATALOG, VesselCatalogEntry, get_vessel_entry, list_vessels

__all__ = [
    "AmbiguousInversionError",
    "FuelCurve",
    "pchip_interpolate",
    "monotonic_cubic_interp",
    "VesselProfile",
    "Axopar28V1",
    "Axopar25CrossTop",
    "Axopar22TTop",
    "Dusky233Evinrude300",
    "RobaloR180",
    "RobaloR160",
    "BostonWhaler210Dauntless",
    "BostonWhaler190Montauk",
    "GradyWhiteFisherman236",
    "VESSEL_CATALOG",
    "VesselCatalogEntry",
    "get_vessel_entry",
    "list_vessels",
]
