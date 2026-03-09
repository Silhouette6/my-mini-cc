"""Code index tools: get_symbol_body with on-demand deep index build.

Uses code-index-mcp (PyPI) for symbol extraction. Builds index automatically
on first use; subsequent calls use cached index.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from langchain_core.tools import tool

import config
from tools.base import safe_path


def _get_project_lock(project_path: str) -> threading.RLock:
    """Get per-project RLock for concurrency safety."""
    with _locks_guard:
        if project_path not in _project_locks:
            _project_locks[project_path] = threading.RLock()
        return _project_locks[project_path]


def _make_mock_context(workdir: Path) -> SimpleNamespace:
    """Build minimal Context for code-index-mcp services."""
    from code_index_mcp.project_settings import ProjectSettings

    ctx = SimpleNamespace()
    ctx.request_context = SimpleNamespace()
    ctx.request_context.lifespan_context = SimpleNamespace(
        base_path=str(workdir),
        settings=ProjectSettings(str(workdir), skip_load=True),
        file_count=0,
    )
    return ctx


def _format_result(result: dict) -> str:
    """Format get_symbol_body result dict as string for tool output."""
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


_project_locks: dict[str, threading.RLock] = {}
_locks_guard = threading.Lock()


@tool
def get_symbol_body(file_path: str, symbol_name: str) -> str:
    """Get the source code of a function, method, or class from a file.

    PREFER this over read_file when you need a specific function/method/class:
    returns only that symbol's code (saves tokens) instead of the entire file.
    Use when user asks: "where is X defined", "show me the implementation of Y",
    "how does Z work", or when you need to inspect a known symbol.

    Supported: functions, classes, methods. NOT supported: module-level variables
    (e.g. WORKER_TYPES, BASE_TOOLS). For constants or config, use read_file instead.

    Args:
        file_path: Path relative to workspace root (e.g. "src/main.py")
        symbol_name: Symbol name (e.g. "main", "MyClass.my_method", "process_data")

    Returns:
        The symbol's code, signature, docstring, and callers. If not found,
        returns available_symbols for retry.
    """
    try:
        # Validate path stays within workspace
        safe_path(file_path)
    except ValueError as e:
        return json.dumps({"status": "error", "message": str(e)})

    workdir = config.settings.workdir
    path_key = str(workdir.resolve())

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
            ctx = _make_mock_context(workdir)
            cache = get_manager_cache()
            manager = cache.get_sqlite_manager(path_key)
            manager.set_project_path(path_key)

            if not manager.load_index():
                try:
                    IndexManagementService(ctx).rebuild_deep_index()
                except Exception as e:
                    return json.dumps({
                        "status": "error",
                        "message": f"Failed to build index: {e}",
                    })

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


CODE_INDEX_TOOLS = [get_symbol_body]
