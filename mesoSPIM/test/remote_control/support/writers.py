"""The TIFF image writer, registered alone: what the commands that name rows need offline.

mesoSPIM finds its writers among the loaded plugin modules (plugins/utils.py). Loading every plugin
folder offline would also load processor plugins that install packages, so the tests load the one
writer module they name rows with, under the plugin prefix, as the registry would.
"""
import importlib.util
import sys
from pathlib import Path

from mesoSPIM.src.plugins.utils import MESOSPIM_PLUGIN_MODULE_PREFIX

TIFF = "Tiff_Writer"


def use_tiff_writer():
    name = MESOSPIM_PLUGIN_MODULE_PREFIX + "TiffWriter"
    if name not in sys.modules:
        path = Path(__file__).resolve().parents[3] / "src" / "plugins" / "ImageWriters" / "TiffWriter.py"
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return TIFF
