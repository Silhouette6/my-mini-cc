"""Unified TaskManager — merges TodoManager (batch API, constraints) with
TaskManager (persistence, dependency graph, auto-unblock)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import config

VALID_STATUSES = {"pending", "in_progress", "completed"}
MAX_ITEMS = 20


class TaskManager:
    """Lightweight batch API + file persistence + blocked_by dependency graph."""

    def __init__(self, tasks_dir: Path | None = None):
        self._dir = tasks_dir or config.settings.tasks_path
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _path(self, tid: str) -> Path:
        return self._dir / f"{tid}.json"

    def _load(self, tid: str) -> dict:
        p = self._path(tid)
        if not p.exists():
            raise ValueError(f"Task '{tid}' not found")
        return json.loads(p.read_text(encoding="utf-8"))

    def _save(self, task: dict) -> None:
        self._path(task["id"]).write_text(
            json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _all_tasks(self) -> list[dict]:
        tasks = []
        for f in sorted(self._dir.glob("*.json")):
            try:
                tasks.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return tasks

    def clear(self) -> None:
        """Remove all task files. Call on startup or reset to start fresh."""
        for f in self._dir.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                continue

    def _unblock_downstream(self, completed_id: str) -> None:
        """Remove *completed_id* from every task's blocked_by list."""
        for f in self._dir.glob("*.json"):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if completed_id in t.get("blocked_by", []):
                t["blocked_by"].remove(completed_id)
                self._save(t)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(self, items: list[dict]) -> str:
        """Batch create / update tasks.

        Each item:
          - id: str         (optional — auto-generated if absent)
          - content: str    (required)
          - status: str     (required — pending | in_progress | completed)
          - blocked_by: list[str]  (optional — ids of blocking tasks)

        Constraints:
          - At most MAX_ITEMS tasks total.
          - At most 1 task in_progress at a time.
          - Completing a task auto-unblocks downstream dependents.

        Returns the rendered task list.
        """
        if not items:
            raise ValueError("items list must not be empty")

        existing = {t["id"]: t for t in self._all_tasks()}
        in_progress_count = sum(
            1 for t in existing.values() if t["status"] == "in_progress"
        )

        for item in items:
            tid = str(item.get("id", "")).strip() or str(uuid.uuid4())[:8]
            is_existing = tid in existing
            prev = existing.get(tid, {})

            content = str(item.get("content", "")).strip() or prev.get("content", "")
            status = str(item.get("status", prev.get("status", "pending"))).strip().lower()
            blocked_by = item.get("blocked_by", prev.get("blocked_by", []))

            if not content:
                raise ValueError(f"Task '{tid}' requires a non-empty 'content'")
            if status not in VALID_STATUSES:
                raise ValueError(f"Invalid status '{status}'")

            was_in_progress = prev.get("status") == "in_progress"
            if status == "in_progress" and not was_in_progress:
                in_progress_count += 1
            if was_in_progress and status != "in_progress":
                in_progress_count -= 1

            prev.update({
                "id": tid,
                "content": content,
                "status": status,
                "blocked_by": [str(b) for b in blocked_by],
            })
            existing[tid] = prev

        if len(existing) > MAX_ITEMS:
            raise ValueError(f"Max {MAX_ITEMS} tasks allowed")
        if in_progress_count > 1:
            # Auto-resolve: keep only the last in_progress (by batch order)
            in_progress_ids = [
                tid for tid, t in existing.items() if t.get("status") == "in_progress"
            ]
            for tid in in_progress_ids[:-1]:
                existing[tid]["status"] = "pending"

        completed_ids = {
            t["id"] for t in existing.values() if t["status"] == "completed"
        }
        for task in existing.values():
            task["blocked_by"] = [
                b for b in task.get("blocked_by", []) if b not in completed_ids
            ]
            self._save(task)

        return self.format_summary(list(existing.values()))

    @staticmethod
    def format_summary(tasks: list[dict]) -> str:
        """Return compact summary: 'OK. N/M completed'."""
        if not tasks:
            return "OK. 0/0 completed"
        done = sum(1 for t in tasks if t.get("status") == "completed")
        return f"OK. {done}/{len(tasks)} completed"

    def list_all(self) -> str:
        """Human-readable rendering of all tasks."""
        tasks = self._all_tasks()
        if not tasks:
            return "No tasks."
        lines: list[str] = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(
                t["status"], "[?]"
            )
            blocked = (
                f" (blocked by: {', '.join(t['blocked_by'])})"
                if t.get("blocked_by")
                else ""
            )
            lines.append(f"{marker} #{t['id']}: {t['content']}{blocked}")
        done = sum(1 for t in tasks if t["status"] == "completed")
        lines.append(f"\n({done}/{len(tasks)} completed)")
        return "\n".join(lines)

    def has_open_items(self) -> bool:
        return any(t["status"] != "completed" for t in self._all_tasks())

    def render_for_prompt(self) -> str:
        """Compact rendering for injection into the system prompt."""
        tasks = self._all_tasks()
        if not tasks:
            return ""
        lines: list[str] = []
        for t in tasks:
            marker = {"pending": "[ ]", "in_progress": "[>]", "completed": "[x]"}.get(
                t["status"], "[?]"
            )
            blocked = f" (blocked: {','.join(t['blocked_by'])})" if t.get("blocked_by") else ""
            lines.append(f"{marker} #{t['id']}: {t['content']}{blocked}")
        return "<current_tasks>\n" + "\n".join(lines) + "\n</current_tasks>"
