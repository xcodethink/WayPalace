"""test_wing_lifecycle.py — 4 D003 wing lifecycle tests.

Each test uses an isolated wing_meta sqlite3 (monkeypatched path) so it
doesn't disturb production wing_meta. The chromadb side does touch the
production collection — but only with throwaway wing names prefixed with
'_test_d003_' so they're easy to identify and clean up.
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
import time

import pytest

sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
import wing_lifecycle


@pytest.fixture
def isolated_wing_db(tmp_path, monkeypatch):
    """Redirect wing_meta DB to tmp_path so tests don't touch production wing_meta."""
    db = tmp_path / "wing_meta_test.sqlite3"
    monkeypatch.setattr(wing_lifecycle, "DB_PATH", str(db))
    return db


@pytest.fixture
def test_wing_name():
    """Unique wing name for this test (auto-cleans after)."""
    name = f"_test_d003_{int(time.time() * 1000) % 1_000_000}"
    yield name
    # Cleanup chromadb after test
    try:
        import memory_core
        col = memory_core.get_collection()
        col.delete(where={"wing": name})
    except Exception:
        pass


# ---------- 1. Auto-create on first store ----------

def test_wing_auto_create_on_first_store(isolated_wing_db, test_wing_name):
    """memory_core.store_chunks must auto-register a brand-new wing in wing_meta."""
    import memory_core
    # Before: wing_meta has no row
    assert wing_lifecycle.get_wing(test_wing_name) is None

    # store_chunks → triggers dual-write
    chunks = [{"text": "Hello D003 auto-create test.", "chunk_index": 0}]
    n = memory_core.store_chunks(test_wing_name, "/tmp/d003_autocreate.md", chunks, time.time())
    assert n == 1

    # After: wing_meta should have a row with last_mine_at set
    row = wing_lifecycle.get_wing(test_wing_name)
    assert row is not None, "wing_meta should auto-register on first store"
    assert row["last_mine_at"] is not None
    assert row["last_mine_at"] > 0


# ---------- 2. Classification 4-tier ----------

def test_wing_review_classification(isolated_wing_db):
    """classify_status must correctly bucket active/dormant/stale/orphan
    based on age + missing_ratio."""
    now = int(time.time())
    DAY = 86400

    # active: 30 days ago, no missing
    active = {"wing_name": "active_test", "created_at": now - 100 * DAY,
              "last_mine_at": now - 30 * DAY, "last_search_at": None,
              "chunk_count": 50, "archived": 0}
    assert wing_lifecycle.classify_status(active, now, missing_ratio=0.0) == "active"

    # dormant: 120 days ago
    dormant = {**active, "last_mine_at": now - 120 * DAY}
    assert wing_lifecycle.classify_status(dormant, now, missing_ratio=0.0) == "dormant"

    # stale: 250 days ago
    stale = {**active, "last_mine_at": now - 250 * DAY}
    assert wing_lifecycle.classify_status(stale, now, missing_ratio=0.0) == "stale"

    # orphan: 400 days ago
    orphan_old = {**active, "last_mine_at": now - 400 * DAY}
    assert wing_lifecycle.classify_status(orphan_old, now, missing_ratio=0.0) == "orphan"

    # orphan via missing_ratio: even if recent, sources all missing → orphan
    orphan_missing = {**active, "last_mine_at": now - 10 * DAY}
    assert wing_lifecycle.classify_status(orphan_missing, now, missing_ratio=1.0) == "orphan"
    assert wing_lifecycle.classify_status(orphan_missing, now, missing_ratio=0.85) == "orphan"
    # Just below threshold — still active
    assert wing_lifecycle.classify_status(orphan_missing, now, missing_ratio=0.79) == "active"

    # orphan via no-activity-ever
    never = {**active, "last_mine_at": 0, "last_search_at": 0}
    assert wing_lifecycle.classify_status(never, now, missing_ratio=0.0) == "orphan"


# ---------- 3. Archive dumps jsonl + soft-deletes wing_meta ----------

def test_wing_archive_dumps_jsonl(isolated_wing_db, test_wing_name, tmp_path, monkeypatch):
    """mp-wing-archive must dump all chunks to jsonl and mark wing_meta.archived=1."""
    import memory_core
    # Populate the wing
    chunks = [
        {"text": "archive test chunk one.", "chunk_index": 0},
        {"text": "archive test chunk two.", "chunk_index": 1},
    ]
    memory_core.store_chunks(test_wing_name, "/tmp/d003_archive.md", chunks, time.time())

    # Redirect archive dir to tmp_path
    import mp_wing_archive
    monkeypatch.setattr(mp_wing_archive, "ARCHIVE_DIR", str(tmp_path / "archive"))

    # Simulate CLI invocation
    monkeypatch.setattr(sys, "argv", ["mp-wing-archive", test_wing_name])
    rc = mp_wing_archive.main()
    assert rc == 0

    # Verify jsonl was created
    archive_files = list((tmp_path / "archive").glob(f"{test_wing_name}-*.jsonl"))
    assert len(archive_files) == 1, f"expected exactly 1 archive jsonl, got {archive_files}"
    content = archive_files[0].read_text()
    lines = [l for l in content.split("\n") if l.strip()]
    assert len(lines) == 2, f"expected 2 chunks dumped, got {len(lines)}"

    # Verify wing_meta soft-delete (archived=1)
    row = wing_lifecycle.get_wing(test_wing_name)
    assert row is not None
    assert row["archived"] == 1
    assert row["archived_at"] is not None


# ---------- 4. Delete requires archived first (unless --force) ----------

def test_wing_delete_requires_archived_first(isolated_wing_db, test_wing_name, monkeypatch):
    """mp-wing-delete must refuse if wing not archived, unless --force."""
    import memory_core
    chunks = [{"text": "delete test.", "chunk_index": 0}]
    memory_core.store_chunks(test_wing_name, "/tmp/d003_delete.md", chunks, time.time())

    # Try delete WITHOUT archive — must refuse with exit code 2
    import mp_wing_delete
    monkeypatch.setattr(sys, "argv",
                        ["mp-wing-delete", test_wing_name, "--confirm"])
    rc = mp_wing_delete.main()
    assert rc == 2, f"expected refusal (exit 2), got {rc}"

    # Verify chunks STILL exist (delete was refused)
    col = memory_core.get_collection()
    items = col.get(where={"wing": test_wing_name}, include=[])
    assert len(items.get("ids", [])) > 0, "chunks should still exist after refused delete"

    # Now archive first
    wing_lifecycle.mark_archived(test_wing_name)

    # Delete with --confirm should succeed
    monkeypatch.setattr(sys, "argv",
                        ["mp-wing-delete", test_wing_name, "--confirm"])
    rc = mp_wing_delete.main()
    assert rc == 0

    # Verify chunks gone
    items_after = col.get(where={"wing": test_wing_name}, include=[])
    assert len(items_after.get("ids", [])) == 0, "chunks should be gone after confirmed delete"
    # And wing_meta row hard-deleted
    assert wing_lifecycle.get_wing(test_wing_name) is None
