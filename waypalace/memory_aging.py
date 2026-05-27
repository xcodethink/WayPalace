#!/usr/bin/env python3
"""
memory_aging.py - memory aging layer (Stage 2)

Tracks dynamic metrics for each chunk in a separate SQLite DB:
- access_count: how many times this chunk was returned by search
- last_accessed: ISO timestamp of last access
- importance: 1-10 manual rating (default 5)
- verified_times: how many times user confirmed "still valid"
- expires_at: explicit expiry date (optional)
- superseded_by: chunk_id that replaces this (optional)
"""
import math
import os
import sqlite3
from datetime import datetime

DB_PATH = os.path.expanduser("~/.mempalace-zh/aging.sqlite3")

DECAY_TABLE = [
    (0, 1.0),
    (7, 1.0),
    (30, 0.9),
    (90, 0.7),
    (180, 0.55),
    (365, 0.4),
    (730, 0.25),
]

PIN_IMPORTANCE = 8
PIN_VERIFIED = 3


import threading
_conn_local = threading.local()


def get_db():
    """Thread-local SQLite connection (needed for the daemon's threaded server)."""
    conn = getattr(_conn_local, "conn", None)
    if conn is not None:
        return conn
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _conn_local.conn = conn
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_aging (
            chunk_id TEXT PRIMARY KEY,
            wing TEXT,
            source_file TEXT,
            created_at TEXT,
            last_accessed TEXT,
            access_count INTEGER DEFAULT 0,
            importance INTEGER DEFAULT 5,
            verified_times INTEGER DEFAULT 0,
            expires_at TEXT,
            superseded_by TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_wing ON chunk_aging(wing)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_last_accessed ON chunk_aging(last_accessed)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_access_count ON chunk_aging(access_count)")

    # V2.1: Usage event log
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL,
            wing TEXT,
            query TEXT,
            mode TEXT,
            similarity REAL,
            rerank_score REAL,
            boosted_score REAL,
            was_useful INTEGER DEFAULT 0,
            context TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_chunk ON chunk_usage_events(chunk_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_created ON chunk_usage_events(created_at)")

    # V2.2: Extend chunk_aging with vitality + status
    _add_column_if_missing(conn, "chunk_aging", "vitality_score", "REAL DEFAULT 10.0")
    _add_column_if_missing(conn, "chunk_aging", "status", "TEXT DEFAULT 'active'")
    _add_column_if_missing(conn, "chunk_aging", "useful_count", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "chunk_aging", "unhelpful_count", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "chunk_aging", "dormant_at", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON chunk_aging(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_vitality ON chunk_aging(vitality_score)")

    # V2.3: Duplicate tracking
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_duplicates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_id TEXT NOT NULL,
            duplicate_of TEXT NOT NULL,
            wing TEXT,
            similarity REAL,
            resolution TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_dup_chunk ON chunk_duplicates(chunk_id)")

    # V2.4: Conflict detection
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chunk_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chunk_a_id TEXT NOT NULL,
            chunk_b_id TEXT NOT NULL,
            wing TEXT,
            conflict_type TEXT,
            severity TEXT,
            description TEXT,
            resolution TEXT,
            resolved_at TEXT,
            detected_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conflict_pair ON chunk_conflicts(chunk_a_id, chunk_b_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conflict_unresolved ON chunk_conflicts(resolved_at)")

    conn.commit()
    return conn


def _add_column_if_missing(conn, table, column, defn):
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {defn}")


def record_access(chunk_ids, wing_map=None):
    """Record search access. wing_map: {chunk_id: (wing, source_file)} for new entries."""
    if not chunk_ids:
        return
    conn = get_db()
    now = datetime.now().isoformat()
    for cid in chunk_ids:
        cursor = conn.execute("""
            UPDATE chunk_aging
            SET access_count = access_count + 1, last_accessed = ?
            WHERE chunk_id = ?
        """, (now, cid))
        if cursor.rowcount == 0:
            wing = ""
            source_file = ""
            if wing_map and cid in wing_map:
                wing, source_file = wing_map[cid]
            conn.execute("""
                INSERT INTO chunk_aging
                    (chunk_id, wing, source_file, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (cid, wing, source_file, now, now))
    conn.commit()


def get_aging_data(chunk_ids):
    """Get aging data for chunk_ids. Missing entries get defaults."""
    if not chunk_ids:
        return {}
    conn = get_db()
    placeholders = ",".join("?" * len(chunk_ids))
    rows = conn.execute(f"""
        SELECT chunk_id, access_count, importance, verified_times,
               last_accessed, expires_at, superseded_by
        FROM chunk_aging
        WHERE chunk_id IN ({placeholders})
    """, chunk_ids).fetchall()
    result = {}
    for row in rows:
        result[row["chunk_id"]] = dict(row)
    defaults = {
        "access_count": 0,
        "importance": 5,
        "verified_times": 0,
        "last_accessed": None,
        "expires_at": None,
        "superseded_by": None,
    }
    for cid in chunk_ids:
        if cid not in result:
            result[cid] = defaults.copy()
    return result


def days_since(iso_ts):
    if not iso_ts:
        return 0
    try:
        dt = datetime.fromisoformat(iso_ts)
        delta = datetime.now() - dt
        return delta.total_seconds() / 86400
    except Exception:
        return 0


def decay_factor(days):
    if days <= 0:
        return 1.0
    for i in range(len(DECAY_TABLE) - 1):
        d0, w0 = DECAY_TABLE[i]
        d1, w1 = DECAY_TABLE[i + 1]
        if days <= d1:
            if d1 == d0:
                return w1
            ratio = (days - d0) / (d1 - d0)
            return w0 + (w1 - w0) * ratio
    return DECAY_TABLE[-1][1]


def compute_score(similarity, aging):
    """Boosted score: similarity x access_boost x verified_boost x decay x importance."""
    access_count = aging.get("access_count", 0)
    importance = aging.get("importance", 5)
    verified_times = aging.get("verified_times", 0)
    last_accessed = aging.get("last_accessed")
    expires_at = aging.get("expires_at")
    superseded = aging.get("superseded_by")

    if superseded:
        return 0.0
    if expires_at:
        try:
            expiry = datetime.fromisoformat(expires_at)
            if datetime.now() > expiry:
                return 0.0
        except Exception:
            pass

    access_boost = 1 + 0.1 * math.log(access_count + 1)
    verified_boost = 1 + 0.05 * verified_times
    importance_mult = importance / 5.0

    if importance >= PIN_IMPORTANCE or verified_times >= PIN_VERIFIED:
        decay = 1.0
    else:
        days = days_since(last_accessed) if last_accessed else 0
        decay = decay_factor(days)

    return similarity * access_boost * verified_boost * decay * importance_mult


def set_importance(chunk_id, value):
    if not 1 <= value <= 10:
        raise ValueError("importance must be 1-10")
    conn = get_db()
    conn.execute("""
        INSERT INTO chunk_aging (chunk_id, importance, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET importance = excluded.importance
    """, (chunk_id, value, datetime.now().isoformat()))
    conn.commit()


def mark_verified(chunk_id):
    conn = get_db()
    cursor = conn.execute("""
        UPDATE chunk_aging
        SET verified_times = verified_times + 1
        WHERE chunk_id = ?
    """, (chunk_id,))
    if cursor.rowcount == 0:
        conn.execute("""
            INSERT INTO chunk_aging (chunk_id, verified_times, created_at)
            VALUES (?, 1, ?)
        """, (chunk_id, datetime.now().isoformat()))
    conn.commit()


def supersede(old_chunk_id, new_chunk_id):
    conn = get_db()
    conn.execute("""
        INSERT INTO chunk_aging (chunk_id, superseded_by, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET superseded_by = excluded.superseded_by
    """, (old_chunk_id, new_chunk_id, datetime.now().isoformat()))
    conn.commit()


def set_expiry(chunk_id, expires_at):
    conn = get_db()
    conn.execute("""
        INSERT INTO chunk_aging (chunk_id, expires_at, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET expires_at = excluded.expires_at
    """, (chunk_id, expires_at, datetime.now().isoformat()))
    conn.commit()


def get_stats():
    conn = get_db()
    row = conn.execute("""
        SELECT
            COUNT(*) as total,
            SUM(access_count) as total_accesses,
            AVG(access_count) as avg_access,
            MAX(access_count) as max_access,
            COUNT(CASE WHEN access_count > 0 THEN 1 END) as accessed,
            COUNT(CASE WHEN importance > 5 THEN 1 END) as boosted,
            COUNT(CASE WHEN expires_at IS NOT NULL THEN 1 END) as has_expiry,
            COUNT(CASE WHEN superseded_by IS NOT NULL THEN 1 END) as superseded
        FROM chunk_aging
    """).fetchone()
    return dict(row) if row else {}


def top_accessed(n=20):
    conn = get_db()
    rows = conn.execute("""
        SELECT chunk_id, wing, source_file, access_count, last_accessed
        FROM chunk_aging
        WHERE access_count > 0
        ORDER BY access_count DESC
        LIMIT ?
    """, (n,)).fetchall()
    return [dict(r) for r in rows]


def stale_memories(days=90):
    conn = get_db()
    rows = conn.execute("""
        SELECT chunk_id, wing, source_file, last_accessed, access_count
        FROM chunk_aging
        WHERE last_accessed IS NOT NULL
        ORDER BY last_accessed ASC
        LIMIT 100
    """).fetchall()
    result = []
    for r in rows:
        d = days_since(r["last_accessed"])
        if d >= days:
            result.append({**dict(r), "days_stale": int(d)})
    return result


# ── V2.1: Usage event logging ─────────────────────────────────────

def log_usage_events(events):
    """
    Record search/retrieval events for each chunk returned.
    events: list of dicts with keys: chunk_id, wing, query, mode, similarity,
            rerank_score (optional), boosted_score (optional), context (optional)
    """
    if not events:
        return
    conn = get_db()
    rows = [
        (
            e.get("chunk_id"), e.get("wing", ""), e.get("query", "")[:500],
            e.get("mode", "isolated"),
            e.get("similarity"), e.get("rerank_score"), e.get("boosted_score"),
            e.get("was_useful", 0), e.get("context", "")[:300],
        )
        for e in events if e.get("chunk_id")
    ]
    conn.executemany("""
        INSERT INTO chunk_usage_events
        (chunk_id, wing, query, mode, similarity, rerank_score, boosted_score, was_useful, context)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()


def mark_useful(chunk_id, was_useful=1):
    """User feedback: was the chunk actually useful? was_useful=1 positive, -1 negative."""
    conn = get_db()
    if was_useful > 0:
        conn.execute("UPDATE chunk_aging SET useful_count = useful_count + 1 WHERE chunk_id = ?", (chunk_id,))
    elif was_useful < 0:
        conn.execute("UPDATE chunk_aging SET unhelpful_count = unhelpful_count + 1 WHERE chunk_id = ?", (chunk_id,))
    # Also mark most recent usage event
    conn.execute("""
        UPDATE chunk_usage_events SET was_useful = ?
        WHERE id = (SELECT id FROM chunk_usage_events WHERE chunk_id = ? ORDER BY id DESC LIMIT 1)
    """, (was_useful, chunk_id))
    conn.commit()


def get_usage_stats(wing=None, days=30):
    """Get usage statistics for the past N days."""
    conn = get_db()
    where = f"created_at >= datetime('now', '-{int(days)} days')"
    params = []
    if wing:
        where += " AND wing = ?"
        params.append(wing)
    row = conn.execute(f"""
        SELECT COUNT(*) as total_events,
               COUNT(DISTINCT chunk_id) as unique_chunks,
               COUNT(DISTINCT query) as unique_queries,
               SUM(CASE WHEN was_useful = 1 THEN 1 ELSE 0 END) as positive_feedback,
               SUM(CASE WHEN was_useful = -1 THEN 1 ELSE 0 END) as negative_feedback,
               AVG(similarity) as avg_similarity
        FROM chunk_usage_events WHERE {where}
    """, params).fetchone()
    return dict(row) if row else {}


# ── V2.2: Vitality score + intelligent forgetting ────────────────

# Vitality bands (tunable)
VITALITY_DORMANT_THRESHOLD = 2.0   # below this → dormant
VITALITY_DORMANT_DAYS = 30         # days stale before becoming dormant
VITALITY_ARCHIVE_DAYS = 60         # days dormant before archived
# Type-based weighting (source file prefix → weight)
TYPE_WEIGHTS = {
    "feedback_": 1.5,
    "user_": 1.3,
    "project_": 0.8,
    "strategy_": 1.1,
    "deployment": 1.0,
    "MEMORY.md": 2.0,  # index files are always high-value
}


def _type_weight_for(source_file):
    if not source_file:
        return 1.0
    base = os.path.basename(source_file)
    for prefix, w in TYPE_WEIGHTS.items():
        if base.startswith(prefix):
            return w
    return 1.0


def compute_vitality(row):
    """
    Compute a 0-100 vitality score for a chunk.
    Higher = more valuable / more active.
    Components:
      - Frequency: access_count / age_days (capped)
      - Recency: exponential decay from last access (30-day half-life)
      - Usefulness: useful_count - unhelpful_count
      - Importance: manual multiplier
      - Type weight: feedback > user > project
    """
    age_days = max(days_since(row.get("created_at")), 1)
    recency_days = days_since(row.get("last_accessed")) if row.get("last_accessed") else age_days
    access_count = row.get("access_count", 0) or 0
    useful = row.get("useful_count", 0) or 0
    unhelpful = row.get("unhelpful_count", 0) or 0
    importance = row.get("importance", 5) or 5

    # Frequency component (0-40): logarithmic, caps growth of noisy high-count chunks
    frequency = min(40, math.log1p(access_count) * 10)

    # Recency component (0-30): exponential decay, 30-day half-life
    recency = 30 * (0.5 ** (recency_days / 30.0))

    # Usefulness component (-10 to +20): explicit feedback dominates
    usefulness = max(-10, min(20, useful * 5 - unhelpful * 3))

    # Importance component (0-20): manual rating * 2
    importance_comp = (importance - 5) * 2 + 10  # 5 → 10, 10 → 20, 1 → 2

    base_score = frequency + recency + usefulness + importance_comp
    type_w = _type_weight_for(row.get("source_file"))

    return max(0.0, base_score * type_w)


def recompute_all_vitality(wing=None):
    """Recompute vitality_score for all (or one wing's) chunks. Returns counts."""
    conn = get_db()
    where = "WHERE 1=1"
    params = []
    if wing:
        where += " AND wing = ?"
        params.append(wing)
    rows = conn.execute(f"SELECT * FROM chunk_aging {where}", params).fetchall()
    updated = 0
    for r in rows:
        v = compute_vitality(dict(r))
        conn.execute("UPDATE chunk_aging SET vitality_score = ? WHERE chunk_id = ?", (v, r["chunk_id"]))
        updated += 1
    conn.commit()
    return updated


def auto_archive_low_vitality():
    """
    Transition low-vitality chunks through lifecycle:
      active → dormant (low vitality + stale >30 days)
      dormant → archived (still stale >60 days)
    Returns dict with counts.
    """
    conn = get_db()
    now = datetime.now().isoformat()

    # active → dormant: vitality below threshold AND stale
    cur = conn.execute(f"""
        UPDATE chunk_aging
        SET status = 'dormant', dormant_at = ?
        WHERE status = 'active'
          AND vitality_score < {VITALITY_DORMANT_THRESHOLD}
          AND (last_accessed IS NULL OR julianday('now') - julianday(last_accessed) > {VITALITY_DORMANT_DAYS})
          AND importance < {PIN_IMPORTANCE}
          AND verified_times < {PIN_VERIFIED}
    """, (now,))
    dormant_count = cur.rowcount

    # dormant → archived: been dormant for N days
    cur = conn.execute(f"""
        UPDATE chunk_aging
        SET status = 'archived'
        WHERE status = 'dormant'
          AND dormant_at IS NOT NULL
          AND julianday('now') - julianday(dormant_at) > {VITALITY_ARCHIVE_DAYS}
          AND importance < {PIN_IMPORTANCE}
    """)
    archived_count = cur.rowcount

    conn.commit()
    return {"dormant": dormant_count, "archived": archived_count}


def get_archived_chunk_ids(wing=None):
    """Return chunk_ids that should be excluded from search."""
    conn = get_db()
    where = "WHERE status = 'archived'"
    params = []
    if wing:
        where += " AND wing = ?"
        params.append(wing)
    rows = conn.execute(f"SELECT chunk_id FROM chunk_aging {where}", params).fetchall()
    return {r["chunk_id"] for r in rows}


def restore_chunk(chunk_id):
    """Restore an archived/dormant chunk to active."""
    conn = get_db()
    conn.execute("""
        UPDATE chunk_aging SET status = 'active', dormant_at = NULL WHERE chunk_id = ?
    """, (chunk_id,))
    conn.commit()


# ── V2.3: Duplicate tracking ─────────────────────────────────────

def record_duplicate(chunk_id, duplicate_of, wing, similarity, resolution):
    """Record that chunk_id was detected as a duplicate of duplicate_of."""
    conn = get_db()
    conn.execute("""
        INSERT INTO chunk_duplicates (chunk_id, duplicate_of, wing, similarity, resolution)
        VALUES (?, ?, ?, ?, ?)
    """, (chunk_id, duplicate_of, wing, similarity, resolution))
    conn.commit()


def get_duplicate_stats():
    conn = get_db()
    row = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN resolution = 'auto_superseded' THEN 1 ELSE 0 END) as superseded,
               SUM(CASE WHEN resolution = 'flagged' THEN 1 ELSE 0 END) as flagged
        FROM chunk_duplicates
    """).fetchone()
    return dict(row) if row else {}


# ── V2.4: Conflict tracking ──────────────────────────────────────

def record_conflict(chunk_a_id, chunk_b_id, wing, conflict_type, severity, description):
    """Record a detected conflict between two chunks."""
    conn = get_db()
    # Check if already recorded (avoid duplicates)
    existing = conn.execute("""
        SELECT id FROM chunk_conflicts
        WHERE (chunk_a_id = ? AND chunk_b_id = ?) OR (chunk_a_id = ? AND chunk_b_id = ?)
    """, (chunk_a_id, chunk_b_id, chunk_b_id, chunk_a_id)).fetchone()
    if existing:
        return existing["id"]
    cur = conn.execute("""
        INSERT INTO chunk_conflicts (chunk_a_id, chunk_b_id, wing, conflict_type, severity, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (chunk_a_id, chunk_b_id, wing, conflict_type, severity, description))
    conn.commit()
    return cur.lastrowid


def get_unresolved_conflicts(wing=None, limit=20):
    conn = get_db()
    where = "WHERE resolved_at IS NULL"
    params = []
    if wing:
        where += " AND wing = ?"
        params.append(wing)
    rows = conn.execute(f"""
        SELECT * FROM chunk_conflicts {where}
        ORDER BY severity DESC, detected_at DESC LIMIT ?
    """, (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def resolve_conflict(conflict_id, resolution):
    conn = get_db()
    conn.execute("""
        UPDATE chunk_conflicts SET resolution = ?, resolved_at = datetime('now') WHERE id = ?
    """, (resolution, conflict_id))
    conn.commit()


# ── V2 stats summary ─────────────────────────────────────────────

def get_v2_stats():
    conn = get_db()
    stats = dict(get_stats())
    lifecycle = conn.execute("""
        SELECT status, COUNT(*) as cnt FROM chunk_aging GROUP BY status
    """).fetchall()
    stats["lifecycle"] = {r["status"]: r["cnt"] for r in lifecycle}
    stats["usage_events_30d"] = get_usage_stats(days=30)
    stats["duplicates"] = get_duplicate_stats()
    unresolved = conn.execute("SELECT COUNT(*) as c FROM chunk_conflicts WHERE resolved_at IS NULL").fetchone()
    stats["unresolved_conflicts"] = unresolved["c"] if unresolved else 0
    return stats


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Memory aging stats")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("stats")
    sub.add_parser("top")
    sub.add_parser("stale")
    sub.add_parser("v2")
    p_vital = sub.add_parser("vitality", help="Recompute vitality scores")
    p_vital.add_argument("--wing")
    sub.add_parser("archive", help="Auto-archive low-vitality chunks")
    args = parser.parse_args()

    if args.cmd == "stats":
        s = get_stats()
        print("=== Memory Aging Stats ===")
        for k, v in s.items():
            print(f"  {k}: {v}")
    elif args.cmd == "top":
        for r in top_accessed():
            print(f"  [{r['access_count']:4d}] {r['wing']:25s} {os.path.basename(r['source_file'])}")
    elif args.cmd == "stale":
        for r in stale_memories():
            print(f"  [{r['days_stale']:4d} days] {r['wing']:25s} {os.path.basename(r['source_file'])}")
    elif args.cmd == "v2":
        import json as _json
        print(_json.dumps(get_v2_stats(), indent=2, ensure_ascii=False))
    elif args.cmd == "vitality":
        n = recompute_all_vitality(args.wing)
        print(f"Recomputed vitality for {n} chunks")
    elif args.cmd == "archive":
        r = auto_archive_low_vitality()
        print(f"Transitioned: {r['dormant']} → dormant, {r['archived']} → archived")
    else:
        parser.print_help()
