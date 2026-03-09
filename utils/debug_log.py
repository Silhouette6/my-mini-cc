"""Debug logging — record each turn's context, model output, and tool results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

_CONTENT_TRUNCATE = 5000
_INPUT_TRUNCATE = 15000  # Larger for prompt engineering analysis


def get_or_create_log_path(workdir: Path, log_dir: str, existing: Path | None) -> Path:
    """Return workdir/log/YYYY-MM-DD_HH-MM-SS.txt. Reuse existing if provided."""
    if existing is not None:
        return existing
    log_root = workdir / log_dir
    log_root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return log_root / f"{stamp}.txt"


def _format_message(msg: BaseMessage, truncate: int = _CONTENT_TRUNCATE) -> str:
    """Convert BaseMessage to readable string."""
    try:
        role = type(msg).__name__.replace("Message", "").lower() if hasattr(msg, "__class__") else "unknown"
    except Exception:
        role = "unknown"
    if hasattr(msg, "name") and getattr(msg, "name", None):
        role = f"{role}:{msg.name}"
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):
        parts = []
        for c in content:
            if hasattr(c, "get") and isinstance(c, dict):
                t = c.get("type", "text")
                v = c.get("text", c.get("content", str(c)))
                parts.append(f"[{t}] {str(v)[:truncate]}")
            else:
                parts.append(str(c)[:truncate])
        content = "\n".join(parts)
    else:
        content = str(content)
    if len(content) > truncate:
        content = content[:truncate] + "\n... (truncated)"
    tool_calls = getattr(msg, "tool_calls", None) or []
    if tool_calls:
        tc_str = ", ".join(
            f"{t.get('name', '?')}({t.get('args', {})})"
            if isinstance(t, dict)
            else str(t)
            for t in tool_calls[:5]
        )
        if len(tool_calls) > 5:
            tc_str += f" ... +{len(tool_calls) - 5} more"
        content = f"[tool_calls: {tc_str}]\n{content}"
    return f"[{role}]\n{content}"


def append_turn_header(
    log_path: Path,
    turn_index: int,
    user_message: str,
) -> None:
    """Write turn header at start of a user turn."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n========== Turn {turn_index} @ {ts} ==========\n")
            f.write(f"User: {user_message[:500]}{'...' if len(user_message) > 500 else ''}\n\n")
    except Exception:
        pass


def append_model_call_log(
    log_path: Path,
    iteration: int,
    system_prompt: str,
    input_messages: list,
    output_messages: list,
) -> None:
    """Append one model call: full input (system + messages) and output."""
    try:
        lines = [
            f"--- Model Call {iteration} ---",
            "",
            ">>> System Prompt (injected before each model call) >>>",
            system_prompt[:30000] + ("\n... (truncated)" if len(system_prompt) > 30000 else ""),
            "",
            ">>> Messages sent to LLM (conversation) >>>",
        ]
        for msg in input_messages:
            lines.append(_format_message(msg, _INPUT_TRUNCATE))
            lines.append("")
        lines.append(">>> Model output >>>")
        for msg in output_messages:
            lines.append(_format_message(msg))
            lines.append("")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def append_tools_log(log_path: Path, iteration: int, tool_messages: list) -> None:
    """Append one tools execution result."""
    try:
        lines = [f"--- Tools Execution {iteration} ---", ""]
        for msg in tool_messages:
            lines.append(_format_message(msg))
            lines.append("")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception:
        pass


def write_turn_log(
    workdir: Path,
    log_dir: str,
    turn_index: int,
    input_messages: list,
    new_messages: list,
    log_path: Path | None = None,
) -> Path | None:
    """Append one turn's log. Returns path used (for caller to cache)."""
    try:
        path = get_or_create_log_path(workdir, log_dir, log_path)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            "",
            f"========== Turn {turn_index} @ {ts} ==========",
            "",
            "--- Input Context ---",
        ]
        for msg in input_messages[-20:]:  # last 20 to avoid huge logs
            lines.append(_format_message(msg))
            lines.append("")
        lines.append("--- New Messages (this turn) ---")
        for msg in new_messages:
            lines.append(_format_message(msg))
            lines.append("")
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path
    except Exception:
        return log_path
