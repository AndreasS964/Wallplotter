"""Wallplotter — Bild-zu-GCode-Toolchain für den V-Plotter im Kletterwand-Keller.

Aufbau nach docs/software-roadmap.md:

* :mod:`wallplotter.pipeline` — SVG/Bild → optimierte Linien (vpype)
* :mod:`wallplotter.gcode` — Linien → GCode (FluidNC/GRBL-Dialekt)
* :mod:`wallplotter.upload` — Upload + Jobsteuerung über die FluidNC-Web-API

CLI (:mod:`wallplotter.cli`) und Web-UI (:mod:`wallplotter.webapp`) sind dünne
Wrapper um genau diese Module — keine doppelte Pipeline.
"""

from .config import FluidNCConfig, PenConfig, PlotConfig
from .gcode import lines_to_gcode, stats_for
from .upload import FluidNCClient, upload_and_run

__version__ = "0.1.0"

__all__ = [
    "FluidNCClient",
    "FluidNCConfig",
    "PenConfig",
    "PlotConfig",
    "__version__",
    "lines_to_gcode",
    "stats_for",
    "upload_and_run",
]
