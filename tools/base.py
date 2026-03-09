"""Base tools: bash, read_file, write_file, edit_file with workspace safety."""

from __future__ import annotations

import subprocess
from pathlib import Path

from google.adk.tools.function_tool import FunctionTool

import config


def safe_path(p: str) -> Path:
    """Resolve *p* relative to workdir; reject escapes."""
    path = (config.settings.workdir / p).resolve()
    if not path.is_relative_to(config.settings.workdir.resolve()):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


def bash(command: str) -> str:
    """Run a shell command in the workspace directory."""
    s = config.settings
    for pattern in s.dangerous_commands:
        if pattern in command:
            return f"Error: Dangerous command blocked (matched '{pattern}')"
    try:
        r = subprocess.run(
            command,
            shell=True,
            cwd=str(s.workdir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=s.command_timeout,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Timeout ({s.command_timeout}s)"


def read_file(
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
    limit: int | None = None,
) -> str:
    """Read a file's contents.

    Args:
        path: File path relative to workspace root.
        start_line: Start line (1-indexed, inclusive). Use with end_line for range.
        end_line: End line (1-indexed, inclusive). Use with start_line for range.
        limit: When no range given, limit to first N lines.
    """
    try:
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        total = len(lines)

        if start_line is not None and end_line is not None:
            start = max(1, start_line) - 1
            end = min(total, end_line)
            if start >= end:
                return f"Error: Invalid range (start_line={start_line} > end_line={end_line})"
            lines = lines[start:end]
        elif limit and limit < total:
            lines = lines[:limit] + [f"... ({total - limit} more lines)"]

        return "\n".join(lines)[:50000]
    except Exception as e:
        return f"Error: {e}"


def write_file(path: str, content: str) -> str:
    """Write content to a file (creates parent directories as needed)."""
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


def edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace the first occurrence of *old_text* with *new_text* in a file."""
    try:
        fp = safe_path(path)
        c = fp.read_text(encoding="utf-8")
        if old_text not in c:
            return f"Error: Text not found in {path}"
        fp.write_text(c.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"


BASE_TOOLS = [
    FunctionTool(bash),
    FunctionTool(read_file),
    FunctionTool(write_file),
    FunctionTool(edit_file),
]
READ_ONLY_TOOLS = [FunctionTool(bash), FunctionTool(read_file)]
