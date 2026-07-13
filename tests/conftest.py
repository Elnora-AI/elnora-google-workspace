"""Conftest for Google Workspace plugin tests — sets up import paths."""

import sys
from pathlib import Path

# Add plugin root to path so 'lib' package resolves correctly
_plugin_root = Path(__file__).resolve().parent.parent

if str(_plugin_root) not in sys.path:
    sys.path.insert(0, str(_plugin_root))

# Also add lib directly for existing flat imports (e.g. 'from auth import ...')
_lib_dir = _plugin_root / "lib"
if str(_lib_dir) not in sys.path:
    sys.path.insert(0, str(_lib_dir))
