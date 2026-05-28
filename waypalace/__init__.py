"""WayPalace — local-first long-term memory for AI coding assistants.

This package is CLI-first / sys.path-based by design. Internal modules use
top-level imports (e.g., ``import memory_core``) rather than relative
imports. The documented usage pattern is to prepend this directory to
``sys.path`` before importing internal modules:

    import sys, os, waypalace
    sys.path.insert(0, os.path.dirname(waypalace.__file__))
    import memory_search

See ``examples/basic_usage.py`` for the canonical pattern.

Library-style import (``from waypalace import memory_search``) is not yet
supported in v0.1.x. Tracked as a roadmap item under "configurable embedding
backend" — refactoring internal imports to relative form is a prerequisite.
"""

__version__ = "0.1.0"
