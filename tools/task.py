"""Task tools — todo_write wrapping the unified TaskManager."""

from __future__ import annotations

import json

from google.adk.tools.function_tool import FunctionTool

from managers.task import TaskManager

_task_mgr: TaskManager | None = None


def _get_mgr() -> TaskManager:
    global _task_mgr
    if _task_mgr is None:
        _task_mgr = TaskManager()
    return _task_mgr


def set_task_manager(mgr: TaskManager) -> None:
    """Allow external injection (used by MiniCC during init)."""
    global _task_mgr
    _task_mgr = mgr


def todo_write(items: str) -> str:
    """Batch create or update tasks.

    *items* is a JSON string — an array of objects, each with:
      - id: str (optional, auto-generated if omitted)
      - content: str (required)
      - status: "pending" | "in_progress" | "completed" (required)
      - blocked_by: list[str] (optional, ids of blocking tasks)

    Constraints: max 20 tasks, at most 1 in_progress.
    Returns 'OK. N/M completed' only; current task board is shown in system prompt.
    """
    try:
        parsed = json.loads(items)
        if not isinstance(parsed, list):
            return "Error: items must be a JSON array"
        return _get_mgr().update(parsed)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON — {e}"
    except Exception as e:
        return f"Error: {e}"


TASK_TOOLS = [FunctionTool(todo_write)]
