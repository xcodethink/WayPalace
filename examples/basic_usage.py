"""basic_usage.py — Minimal WayPalace usage in pure Python.

Assumes:
  - waypalace package installed (pip install -e . from repo root)
  - daemon is running (or you accept cold-start latency)

Demonstrates: mine a file, then search it back.
"""
from __future__ import annotations

import os
import sys
import tempfile

# Resolve waypalace package path (adjust to your install location)
sys.path.insert(0, os.environ.get("WAYPALACE_HOME", os.path.expanduser("~/.waypalace")) + "/waypalace")

import memory_mine  # noqa: E402
import memory_search  # noqa: E402

# 1. Create a test memory file
with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
    f.write("""# Test memory

When deploying any web app, always verify the OAuth callback URLs match
your production domain before pushing. A common source of bugs.
""")
    test_file = f.name

# 2. Mine it into the 'global' namespace
result = memory_mine.mine_file(test_file, wing="global", verbose=False)
print(f"Mine result: {result}")

# 3. Search for it
search_result = memory_search.search_isolated(
    query="OAuth callback deploy",
    current_wing="global",
    n_results=3,
    threshold=0.4,
    detail_level="summary",
)
print(f"Found {search_result['returned']} results:")
for r in search_result["results"]:
    print(f"  [{r['similarity']:.3f}] {r.get('source_name')}: {r.get('summary', r.get('text', ''))[:120]}")

# Cleanup
os.unlink(test_file)
