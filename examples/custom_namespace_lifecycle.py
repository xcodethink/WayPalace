"""custom_namespace_lifecycle.py — Inspect and clean up a namespace.

Shows the full lifecycle: create → mine → review → archive → delete.
DO NOT run this against a real namespace you care about — it deletes data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

sys.path.insert(0, os.environ.get("WAYPALACE_HOME", os.path.expanduser("~/.waypalace")) + "/waypalace")

import memory_mine  # noqa: E402
import memory_core  # noqa: E402
import wing_lifecycle  # noqa: E402

NS = f"_example_lifecycle_{int(time.time())}"

# 1. Create + populate
f = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False)
f.write("# Example namespace\n\nTransient memory used in the lifecycle example.\n")
f.close()
memory_mine.mine_file(f.name, wing=NS, verbose=False)
print(f"Created namespace {NS!r}")

# 2. Inspect (live count)
col = memory_core.get_collection()
items = col.get(where={"wing": NS}, include=[])
print(f"  Chunks: {len(items.get('ids', []))}")

# 3. Check metadata + status
row = wing_lifecycle.get_wing(NS)
audit = wing_lifecycle.audit_wing_sources(NS)
status = wing_lifecycle.classify_status(row, missing_ratio=audit["missing_ratio"],
                                         asset_exists=audit["developer_dir_exists"])
print(f"  Status: {status} (created just now, so 'active')")

# 4. Soft-delete (archive)
wing_lifecycle.mark_archived(NS)
print(f"  Marked archived")

# 5. Hard-delete (purge from ChromaDB + metadata)
col.delete(where={"wing": NS})
wing_lifecycle.hard_delete(NS)
items_after = col.get(where={"wing": NS}, include=[])
assert len(items_after.get("ids", [])) == 0
assert wing_lifecycle.get_wing(NS) is None
print(f"  Purged. Namespace is gone.")

# Cleanup
os.unlink(f.name)
