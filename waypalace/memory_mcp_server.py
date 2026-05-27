#!/usr/bin/env python3
"""memory_mcp_server.py - MCP server for local memory system"""
import json
import os
import sys

# Add scripts dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import memory_core
import memory_search
import memory_mine
import memory_timeline


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def log(msg):
    sys.stderr.write(f"[memory-mcp] {msg}\n")
    sys.stderr.flush()


# ========== Tool definitions ==========

TOOLS = [
    {
        "name": "memory_search",
        "description": (
            "Search the local memory system. Returns relevant Chinese/English memories from "
            "the current project's wing AND the global wing. Use this BEFORE answering questions "
            "about past decisions, configurations, deployments, bugs, or any project-specific "
            "knowledge. The search uses semantic matching with bge-m3 embeddings, so you can "
            "search in natural language. Default threshold is 0.5 - results below that are "
            "filtered out as 'not found'.\n\n"
            "TOKEN OPTIMIZATION: pass detail_level='index' to first scan cheap snippets "
            "(~80 chars each, ~6x fewer tokens), then call memory_get with the chunk_ids you "
            "actually want to read in full. Use 'summary' for a middle ground (~300 chars). "
            "Default is 'full' for backward compatibility."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query in any language"},
                "current_wing": {
                    "type": "string",
                    "description": "Optional: project wing to search (e.g. '<project-a>'). If omitted, auto-detected from cwd."
                },
                "limit": {"type": "integer", "description": "Number of results (default 5)"},
                "threshold": {"type": "number", "description": "Similarity threshold (default 0.5)"},
                "detail_level": {
                    "type": "string",
                    "enum": ["index", "summary", "full"],
                    "description": "Result detail: 'index' (~80-char snippets, cheapest), 'summary' (~300 chars), 'full' (default, full chunk text)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_search_all",
        "description": (
            "Cross-project search. Searches ALL wings, not just current project + global. "
            "USE WITH CAUTION: results from other projects may contain configuration values "
            "(API keys, URLs, project IDs) that MUST NOT be copied to the current project. "
            "Only borrow GENERAL PATTERNS, never SPECIFIC VALUES. Returns results grouped by wing.\n\n"
            "Supports the same detail_level parameter as memory_search."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "Default 10"},
                "threshold": {"type": "number", "description": "Default 0.4"},
                "detail_level": {
                    "type": "string",
                    "enum": ["index", "summary", "full"],
                    "description": "Result detail: 'index' / 'summary' / 'full' (default)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "memory_timeline",
        "description": (
            "Browse memory chunks ordered by when they were filed (filed_at metadata), "
            "optionally filtered by wing and date range. Use when keyword search misses the "
            "point — e.g. 'what did I record last week', 'recent feedback in <project-b>', "
            "'lessons logged during the HK channel push'. Returns lightweight snippets; "
            "pair with memory_get to pull full text for chunks you actually want to read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Restrict to a wing (omit for all wings)"},
                "start": {"type": "string", "description": "ISO lower bound, e.g. '2026-05-01'"},
                "end": {"type": "string", "description": "ISO upper bound, e.g. '2026-05-13'"},
                "limit": {"type": "integer", "description": "Max results (default 50, max 500)"}
            }
        }
    },
    {
        "name": "memory_get",
        "description": (
            "Fetch full chunk text by chunk_id. Designed to pair with memory_search "
            "(detail_level='index') or memory_timeline: first scan cheap snippets, then "
            "pull the full text only for the chunks that matter. This avoids the 'open 10 "
            "full chunks on every search' pattern that wastes context. Cross-wing lookup — "
            "caller is responsible for not copying specific values across projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of chunk_id values to fetch"
                }
            },
            "required": ["ids"]
        }
    },
    {
        "name": "memory_status",
        "description": "Get the status of the local memory system: total chunks, wings, file counts per wing.",
        "inputSchema": {"type": "object", "properties": {}}
    },
    {
        "name": "memory_refresh",
        "description": (
            "Re-mine a directory or file into the memory system. Incremental: skips files that "
            "haven't changed since last mine. Use when you've added new memories or want to update existing ones."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute path to file or directory"},
                "wing": {"type": "string", "description": "Target wing name"},
                "force": {"type": "boolean", "description": "Force re-mine all files (default false)"}
            },
            "required": ["path", "wing"]
        }
    },
    {
        "name": "memory_remember",
        "description": (
            "Manually save a piece of content to memory. Writes to a markdown file in the appropriate "
            "memory directory and immediately indexes it. Use when the user says 'remember this' or "
            "when you've discovered an important decision/preference that should persist."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to remember"},
                "wing": {"type": "string", "description": "Target wing (project name or 'global')"},
                "title": {"type": "string", "description": "Short title for the memory"},
                "memory_type": {
                    "type": "string",
                    "description": "Type: feedback / project / reference / user",
                    "enum": ["feedback", "project", "reference", "user"]
                }
            },
            "required": ["content", "wing", "title", "memory_type"]
        }
    },
    {
        "name": "memory_lesson",
        "description": (
            "Save a lesson learned (bug fix, debugging discovery, etc.) to BOTH the project wing "
            "(detailed version with specific values) AND the global wing (generalized pattern with "
            "no project-specific details). This is the key mechanism for cross-project learning - "
            "the global version helps prevent the same mistake in other projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_wing": {"type": "string", "description": "Current project wing"},
                "title": {"type": "string", "description": "Short lesson title"},
                "specific_version": {"type": "string", "description": "Detailed version with project-specific context"},
                "general_version": {"type": "string", "description": "Generalized pattern - NO API keys, URLs, project IDs, or specific values. Only the principle."}
            },
            "required": ["project_wing", "title", "specific_version", "general_version"]
        }
    }
]


# ========== Tool handlers ==========

def _normalize_detail(value):
    if value in ("index", "summary", "full"):
        return value
    return "full"


# G2 (2026-05-26): MCP default detail_level changed from "full" → "index".
# Rationale: Claude is the typical caller; it should pay the cheapest read first
# and follow up with memory_get for chunks it actually wants in full. This is
# the progressive disclosure pattern from claude-mem (11-18x token savings on
# code navigation). CLI users still default to "full" via mp-search --detail.
_MCP_DEFAULT_DETAIL = "index"


def handle_memory_search(args):
    query = args.get("query", "")
    current_wing = args.get("current_wing")
    limit = int(args.get("limit", 5))
    threshold = float(args.get("threshold", 0.5))
    detail_level = _normalize_detail(args.get("detail_level", _MCP_DEFAULT_DETAIL))
    hybrid = bool(args.get("hybrid", False))  # Phase 2b opt-in

    result = memory_search.search_isolated(
        query=query,
        current_wing=current_wing,
        n_results=limit,
        threshold=threshold,
        detail_level=detail_level,
        hybrid=hybrid,
    )
    return result


def handle_memory_search_all(args):
    query = args.get("query", "")
    limit = int(args.get("limit", 10))
    threshold = float(args.get("threshold", 0.4))
    detail_level = _normalize_detail(args.get("detail_level", _MCP_DEFAULT_DETAIL))
    hybrid = bool(args.get("hybrid", False))  # Phase 2b opt-in

    result = memory_search.search_all(
        query=query,
        n_results=limit,
        threshold=threshold,
        detail_level=detail_level,
        hybrid=hybrid,
    )
    return result


def handle_memory_timeline(args):
    wing = args.get("wing")
    start = args.get("start")
    end = args.get("end")
    limit = int(args.get("limit", 50))
    return memory_timeline.list_timeline(wing=wing, start=start, end=end, limit=limit)


def handle_memory_get(args):
    ids = args.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    return memory_timeline.get_by_ids(ids)


def handle_memory_status(args):
    stats = memory_core.get_wing_stats()
    total = memory_core.get_total_count()
    return {
        "total_chunks": total,
        "wings": stats,
        "wing_count": len(stats),
    }


def handle_memory_refresh(args):
    path = os.path.expanduser(args.get("path", ""))
    wing = args.get("wing", "")
    force = bool(args.get("force", False))

    if not path or not wing:
        return {"error": "path and wing are required"}

    if os.path.isfile(path):
        result = memory_mine.mine_file(path, wing, force=force, verbose=False)
        return {"file": path, "wing": wing, "result": result}
    elif os.path.isdir(path):
        stats = memory_mine.mine_directory(path, wing, force=force, verbose=False)
        # Strip large lists
        stats.pop("blocked_files", None)
        stats.pop("error_files", None)
        return stats
    else:
        return {"error": f"path not found: {path}"}


def handle_memory_remember(args):
    """
    Save content to a memory file and immediately index it.
    Determines target directory based on wing.
    """
    from datetime import datetime

    content = args.get("content", "")
    wing = args.get("wing", "")
    title = args.get("title", "untitled")
    memory_type = args.get("memory_type", "project")

    if not content or not wing:
        return {"error": "content and wing are required"}

    # Determine target directory
    if wing == "global":
        target_dir = os.path.expanduser("~/.claude/skills/_user_memory")
    else:
        # Find matching project memory dir
        target_dir = os.path.expanduser(f"~/.claude/projects/-user-workspace-{wing}/memory")
        if not os.path.isdir(target_dir):
            # Try lowercase variant scan
            projects_root = os.path.expanduser("~/.claude/projects")
            target_dir = None
            if os.path.isdir(projects_root):
                for d in os.listdir(projects_root):
                    if wing.lower() in d.lower():
                        candidate = os.path.join(projects_root, d, "memory")
                        if os.path.isdir(candidate):
                            target_dir = candidate
                            break
            if target_dir is None:
                return {"error": f"Could not find memory directory for wing: {wing}"}

    os.makedirs(target_dir, exist_ok=True)

    # Generate filename
    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60]
    filename = f"{memory_type}_{safe_title}.md"
    file_path = os.path.join(target_dir, filename)

    # Write with frontmatter
    file_content = f"""---
name: {title}
description: {title}
type: {memory_type}
created_at: {datetime.now().isoformat()}
---

{content}
"""
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(file_content)

    # Index immediately
    result = memory_mine.mine_file(file_path, wing, force=True, verbose=False)

    return {
        "file": file_path,
        "wing": wing,
        "indexed": result.get("status") == "indexed",
        "chunks": result.get("chunks", 0),
    }


def handle_memory_lesson(args):
    """
    Save a lesson to BOTH project wing (specific) and global wing (generalized).
    """
    from datetime import datetime

    project_wing = args.get("project_wing", "")
    title = args.get("title", "untitled")
    specific = args.get("specific_version", "")
    general = args.get("general_version", "")

    if not all([project_wing, title, specific, general]):
        return {"error": "project_wing, title, specific_version, and general_version are all required"}

    results = {"project": None, "global": None}

    # 1. Save specific version to project
    proj_args = {
        "content": f"## {title}\n\n{specific}",
        "wing": project_wing,
        "title": f"lesson_{title}",
        "memory_type": "feedback",
    }
    results["project"] = handle_memory_remember(proj_args)

    # 2. Save general version to global
    global_dir = os.path.expanduser("~/.claude/skills/06-部署和运维/踩坑记录")
    os.makedirs(global_dir, exist_ok=True)

    safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in title)[:60]
    global_file = os.path.join(global_dir, f"通用经验_{safe_title}.md")

    global_content = f"""---
name: {title}
description: 通用经验 - {title}
type: lesson
scope: universal
created_at: {datetime.now().isoformat()}
---

## {title}

{general}

> 注: 这是通用模式，已去除项目特定细节。具体细节见对应项目的 lessons.md
"""
    with open(global_file, "w", encoding="utf-8") as f:
        f.write(global_content)

    global_result = memory_mine.mine_file(global_file, "global", force=True, verbose=False)
    results["global"] = {
        "file": global_file,
        "wing": "global",
        "indexed": global_result.get("status") == "indexed",
        "chunks": global_result.get("chunks", 0),
    }

    return results


# ========== Tool dispatcher ==========

TOOL_HANDLERS = {
    "memory_search": handle_memory_search,
    "memory_search_all": handle_memory_search_all,
    "memory_timeline": handle_memory_timeline,
    "memory_get": handle_memory_get,
    "memory_status": handle_memory_status,
    "memory_refresh": handle_memory_refresh,
    "memory_remember": handle_memory_remember,
    "memory_lesson": handle_memory_lesson,
}


# ========== MCP protocol ==========

def handle_message(msg):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "memory", "version": "1.0.0"},
            }
        })

    elif method == "notifications/initialized":
        pass  # No response

    elif method == "tools/list":
        send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOLS}
        })

    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"}
            })
            return

        try:
            result = handler(tool_args)
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}
                    ]
                }
            })
        except Exception as e:
            log(f"Tool {tool_name} error: {e}")
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32603, "message": f"Tool error: {e}"}
            })

    else:
        if msg_id is not None:
            send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Unknown method: {method}"}
            })


def main():
    log("Server started")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception as e:
            log(f"JSON parse error: {e}")
            continue
        try:
            handle_message(msg)
        except Exception as e:
            log(f"Handle error: {e}")


if __name__ == "__main__":
    main()
