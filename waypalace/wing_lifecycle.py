#!/usr/bin/env python3
"""wing_lifecycle.py — D003 Wing Lifecycle Management shared API.

Manages the wing_meta SQLite table: a lightweight registry tracking wing
creation time, last activity (mine/search), chunk count, source-file
health, and archive state.

Design rules:
- Separate sqlite (`wing_meta.sqlite3`) from `aging.sqlite3` to avoid
  lock contention with the hot search/mine path.
- All write functions are idempotent and use INSERT OR IGNORE / UPDATE
  patterns.
- All writes are wrapped in try/except with a fail-silent option for
  embedding inside hot paths (memory_core.store_chunks).
- 4-tier status classification per D003 ADR Implementation Findings:
  active (<90d) / dormant (90-180d) / stale (180-365d) / orphan
  (365+ OR source-missing ratio >= 0.8 OR no activity ever).
"""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = "${WAYPALACE_DATA}/wing_meta.sqlite3"

# Status thresholds (days)
ACTIVE_DAYS = 90
DORMANT_DAYS = 180
STALE_DAYS = 365
ORPHAN_MISSING_RATIO = 0.8

SCHEMA = """
CREATE TABLE IF NOT EXISTS wing_meta (
    wing_name        TEXT PRIMARY KEY,
    source_dir       TEXT,
    created_at       INTEGER NOT NULL,
    last_mine_at     INTEGER,
    last_search_at   INTEGER,
    chunk_count      INTEGER DEFAULT 0,
    source_machine   TEXT DEFAULT '<workstation>',
    notes            TEXT,
    archived         INTEGER DEFAULT 0,
    archived_at      INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wing_active ON wing_meta(last_mine_at, archived);
CREATE INDEX IF NOT EXISTS idx_wing_archived ON wing_meta(archived);
"""


@contextmanager
def _connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ---------- wing name normalization ----------

def normalize_wing_name(name: str) -> str:
    """Canonical wing name: lowercase + '-'/' ' → '_'.

    Used by both the auto-create path (refresh-memory.sh glob) and any
    place that needs to compare wings. Pure function, no I/O.
    """
    return name.lower().replace("-", "_").replace(" ", "_").strip("_")


def developer_project_exists(wing: str) -> bool:
    """Check if any ~/Developer/<X> directory normalizes to this wing name.

    Important orphan-detection signal (D003 v1.1): if the actual project
    asset directory still exists, the wing should NOT be marked orphan
    even if source_file paths in ~/.claude/projects/ are missing.

    Per industry best practice (GitHub stale bot / GitLab archive policy):
    the existence of the asset itself overrides historical-activity signals.
    """
    dev_root = os.path.expanduser("~/Developer")
    if not os.path.isdir(dev_root):
        return False
    for entry in os.listdir(dev_root):
        # Skip non-project conventions
        if entry.startswith(".") or entry.startswith("_"):
            continue
        if normalize_wing_name(entry) == wing:
            return True
    return False


# ---------- write APIs (call from hot path; fail-silent) ----------

def register_wing(wing: str, source_dir: str | None = None) -> bool:
    """INSERT OR IGNORE a wing row. Returns True if a new row was inserted.

    Safe to call before every store_chunks — idempotent at the SQL level.
    Fail-silent: any DB error is swallowed and False is returned.
    """
    try:
        _init_db()
        now = int(time.time())
        with _connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO wing_meta (wing_name, source_dir, created_at) "
                "VALUES (?, ?, ?)",
                (wing, source_dir, now),
            )
            return cur.rowcount > 0
    except Exception:
        return False


def update_wing_activity(wing: str, event: str, chunk_delta: int = 0) -> None:
    """Update last_mine_at / last_search_at + optional chunk_count delta.

    event ∈ {"mine", "search"}. Idempotent. Fail-silent.
    """
    if event not in ("mine", "search"):
        return
    try:
        _init_db()
        # ensure row exists first
        register_wing(wing)
        now = int(time.time())
        field = "last_mine_at" if event == "mine" else "last_search_at"
        with _connect() as conn:
            if chunk_delta:
                conn.execute(
                    f"UPDATE wing_meta SET {field} = ?, chunk_count = chunk_count + ? "
                    "WHERE wing_name = ?",
                    (now, chunk_delta, wing),
                )
            else:
                conn.execute(
                    f"UPDATE wing_meta SET {field} = ? WHERE wing_name = ?",
                    (now, wing),
                )
    except Exception:
        pass  # fail-silent — observability/lifecycle must not break hot path


# ---------- read APIs (used by CLIs) ----------

def list_wings(include_archived: bool = False) -> list[dict]:
    """Return all wing_meta rows as dicts, sorted by chunk_count desc."""
    _init_db()
    with _connect() as conn:
        where = "" if include_archived else "WHERE archived = 0"
        rows = conn.execute(
            f"SELECT * FROM wing_meta {where} ORDER BY chunk_count DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def get_wing(wing: str) -> dict | None:
    _init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM wing_meta WHERE wing_name = ?", (wing,)
        ).fetchone()
        return dict(row) if row else None


# ---------- status classification (per D003 Implementation Findings) ----------

def classify_status(wing_row: dict, now: int | None = None,
                    missing_ratio: float | None = None,
                    asset_exists: bool | None = None) -> str:
    """4-tier status: active / dormant / stale / orphan.

    Per D003 ADR §G.findings + v1.1 amendment:
      - asset_exists=True (~/Developer/<X> alive) → downgrade would-be orphan to dormant
        (industry best practice: asset existence overrides historical-activity signal —
         see GitHub stale-bot vs GitLab archive policy)
      - orphan: (missing_ratio >= 0.8 OR no activity ever OR age > 365d) AND asset_exists=False
      - active: < 90 days
      - dormant: 90-180 days (or would-be-orphan but asset_exists=True)
      - stale: 180-365 days
    """
    if wing_row.get("archived"):
        return "orphan"  # already archived; treat as orphan for display

    if now is None:
        now = int(time.time())

    def _downgrade(s: str) -> str:
        """If asset_exists, would-be orphan becomes dormant instead."""
        if s == "orphan" and asset_exists:
            return "dormant"
        return s

    if missing_ratio is not None and missing_ratio >= ORPHAN_MISSING_RATIO:
        return _downgrade("orphan")

    last_mine = wing_row.get("last_mine_at") or 0
    last_search = wing_row.get("last_search_at") or 0
    last_active = max(last_mine, last_search)

    if last_active <= 0:
        # Never observed any activity in wing_meta — fall back to orphan
        return _downgrade("orphan")

    age_days = (now - last_active) / 86400.0
    if age_days > STALE_DAYS:
        return _downgrade("orphan")
    if age_days < ACTIVE_DAYS:
        return "active"
    if age_days < DORMANT_DAYS:
        return "dormant"
    return "stale"


# ---------- archive / delete APIs (called by mp-wing-archive / -delete) ----------

def mark_archived(wing: str) -> None:
    """Soft-delete: set archived=1, archived_at=now."""
    _init_db()
    now = int(time.time())
    with _connect() as conn:
        conn.execute(
            "UPDATE wing_meta SET archived = 1, archived_at = ? WHERE wing_name = ?",
            (now, wing),
        )


def hard_delete(wing: str) -> None:
    """Remove the row entirely. Only after archive flow has run."""
    _init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM wing_meta WHERE wing_name = ?", (wing,))


def sync_chunk_count(wing: str, real_count: int) -> None:
    """Write the live chromadb chunk count back to wing_meta. Idempotent.

    Called by mp-wings-review after it does the live query for free, so the
    cached chunk_count gradually converges to truth without extra IO.
    """
    try:
        _init_db()
        with _connect() as conn:
            conn.execute(
                "UPDATE wing_meta SET chunk_count = ? WHERE wing_name = ?",
                (real_count, wing),
            )
    except Exception:
        pass  # fail-silent


# ---------- source-file health audit (orphan detection) ----------

def audit_wing_sources(wing: str) -> dict:
    """Inspect chromadb chunks for this wing, return source file health summary.

    Returns: {chunks, unique_sources, sources_exist, sources_missing,
              missing_ratio, last_source_mtime, developer_dir_exists}
    Pure read (no writes). Used by mp-wings-review to compute orphan signals.

    developer_dir_exists (D003 v1.1): True if ~/Developer/<X> for any X that
    normalizes to this wing name still exists locally — overrides orphan
    classification (asset still alive).
    """
    out = {
        "chunks": 0, "unique_sources": 0, "sources_exist": 0,
        "sources_missing": 0, "missing_ratio": 0.0,
        "last_source_mtime": None,
        "developer_dir_exists": developer_project_exists(wing),
    }
    try:
        import sys
        sys.path.insert(0, "${WAYPALACE_HOME}/scripts")
        import memory_core
        col = memory_core.get_collection()
        items = col.get(where={"wing": wing}, include=["metadatas"])
        metas = items.get("metadatas", []) or []
        out["chunks"] = len(metas)
        if not metas:
            return out

        sources = set()
        mtimes = []
        for m in metas:
            sf = m.get("source_file")
            if sf:
                sources.add(sf)
            mt = m.get("source_mtime")
            if mt:
                try:
                    mtimes.append(float(mt))
                except (TypeError, ValueError):
                    pass

        out["unique_sources"] = len(sources)
        out["sources_exist"] = sum(1 for s in sources if os.path.exists(s))
        out["sources_missing"] = out["unique_sources"] - out["sources_exist"]
        if out["unique_sources"] > 0:
            out["missing_ratio"] = round(out["sources_missing"] / out["unique_sources"], 3)
        if mtimes:
            out["last_source_mtime"] = max(mtimes)
        return out
    except Exception:
        return out


if __name__ == "__main__":
    # Smoke test
    _init_db()
    print(f"wing_meta sqlite at: {DB_PATH}")
    print(f"Existing rows: {len(list_wings())}")
