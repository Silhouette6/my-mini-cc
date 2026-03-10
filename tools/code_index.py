"""Code index tools: get_symbol_body with on-demand deep index build."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from google.adk.tools.function_tool import FunctionTool

import config
from tools.base import safe_path

_project_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()

# Projects that have been (re)indexed during the current process lifetime.
# On first access per session we always rebuild so the index reflects the
# current state of the project (stale on-disk DBs from previous sessions are
# ignored).  After a successful build the key is added here so subsequent
# calls within the same session skip the expensive rebuild.
_indexed_this_session: set[str] = set()


def _get_project_lock(project_path: str) -> threading.RLock:
    with _locks_guard:
        if project_path not in _project_locks:
            _project_locks[project_path] = threading.RLock()
        return _project_locks[project_path]


def _make_mock_context(path_key: str) -> SimpleNamespace:
    """Build a minimal MCP-style context for IndexManagement/CodeIntelligence services.

    Uses *path_key* (the resolved absolute path) for both base_path and
    ProjectSettings so that all services hash the project to the same DB.
    """
    from code_index_mcp.project_settings import ProjectSettings

    ctx = SimpleNamespace()
    ctx.request_context = SimpleNamespace()
    ctx.request_context.lifespan_context = SimpleNamespace(
        base_path=path_key,          # must match path_key so hashes align
        settings=ProjectSettings(path_key, skip_load=True),
        file_count=0,
    )
    return ctx


def _format_result(result: dict) -> str:
    if result.get("status") == "success":
        parts = [f"**{result.get('symbol_name', '?')}** ({result.get('type', '?')})"]
        if result.get("signature"):
            parts.append(f"Signature: `{result['signature']}`")
        if result.get("docstring"):
            parts.append(f"Docstring: {result['docstring']}")
        parts.append(f"\n```\n{result.get('code', '')}\n```")
        if result.get("called_by"):
            parts.append(f"\nCalled by: {', '.join(result['called_by'])}")
        return "\n".join(parts)
    return json.dumps(result, ensure_ascii=False, indent=2)


def get_symbol_body(file_path: str, symbol_name: str) -> str:
    """Get the source code of a function, method, or class from a file.

    PREFER this over read_file when you need a specific function/method/class:
    returns only that symbol's code (saves tokens) instead of the entire file.
    Use when user asks: "where is X defined", "show me the implementation of Y",
    "how does Z work", or when you need to inspect a known symbol.

    Supported: functions, classes, methods. NOT supported: module-level variables.
    For constants or config, use read_file instead.

    Args:
        file_path: Path relative to workspace root (e.g. "src/main.py")
        symbol_name: Symbol name (e.g. "main", "MyClass.my_method", "process_data")

    Returns:
        The symbol's code, signature, docstring, and callers. If not found,
        returns available_symbols for retry.
    """
    try:
        safe_path(file_path)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})

    # Use the resolved absolute path as the canonical key everywhere so that
    # _make_mock_context, cache lookups, and _hash_project_path all hash to
    # the same SQLite database file.
    path_key = str(config.settings.workdir.resolve())

    try:
        from code_index_mcp.project_manager_cache import get_manager_cache
        from code_index_mcp.request_context import RequestContextManager
        from code_index_mcp.services.code_intelligence_service import CodeIntelligenceService
        from code_index_mcp.services.index_management_service import IndexManagementService
    except ImportError as e:
        return json.dumps({
            "status": "error",
            "message": f"code-index-mcp not installed: {e}. Run: pip install code-index-mcp",
        })

    with _get_project_lock(path_key):
        with RequestContextManager(path_key):
            ctx = _make_mock_context(path_key)
            cache = get_manager_cache()
            manager = cache.get_sqlite_manager(path_key)

            needs_build = path_key not in _indexed_this_session

            if needs_build:
                # Always (re)build once per session so stale on-disk DBs from
                # previous runs don't silently serve wrong or missing files.
                manager.set_project_path(path_key)
                try:
                    IndexManagementService(ctx).rebuild_deep_index()
                    _indexed_this_session.add(path_key)
                except Exception as e:
                    return json.dumps({
                        "status": "error",
                        "message": f"Failed to build index: {e}",
                    })
            else:
                # Index already fresh for this session; just ensure the manager
                # is initialised (set_project_path is idempotent for same path).
                manager.set_project_path(path_key)
                manager.load_index()

            try:
                result = CodeIntelligenceService(ctx).get_symbol_body(file_path, symbol_name)
                return _format_result(result)
            except Exception as e:
                return json.dumps({
                    "status": "error",
                    "message": str(e),
                    "file_path": file_path,
                    "symbol_name": symbol_name,
                })


def invalidate_session_index() -> None:
    """Force a fresh rebuild on the next get_symbol_body call.

    Called by MiniCC.reset() / _clear_startup_caches so that a /reset or
    new session always re-indexes from scratch.
    """
    _indexed_this_session.clear()


CODE_INDEX_TOOLS = [FunctionTool(get_symbol_body)]
