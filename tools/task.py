"""Task tools — todo_write and todo_list wrapping the unified TaskManager."""

from __future__ import annotations

import json

from langchain_core.tools import StructuredTool, tool

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


def _todo_write(items: str) -> str:
    """Batch create or update tasks.

    *items* is a JSON string — an array of objects, each with:
      - id: str (optional, auto-generated if omitted)
      - content: str (required)
      - status: "pending" | "in_progress" | "completed" (required)
      - blocked_by: list[str] (optional, ids of blocking tasks)

    Constraints: max 20 tasks, at most 1 in_progress.
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


todo_write = StructuredTool.from_function(
    func=_todo_write,
    name="todo_write",
    description=(
        "Batch create or update tasks. Pass a JSON array string of items, "
        "each with: id (optional), content (required), status (required: "
        "pending|in_progress|completed), blocked_by (optional: list of task ids). "
        "Max 20 tasks, at most 1 in_progress."
    ),
)


@tool
def todo_list() -> str:
    """List all current tasks with their status and dependencies."""
    return _get_mgr().list_all()


TASK_TOOLS = [todo_write, todo_list]
