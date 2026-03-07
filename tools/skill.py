"""Skill tools — load_skill and run_skill_tool (proxy-tool pattern)."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from managers.skill import SkillLoader

_skill_loader: SkillLoader | None = None


def _get_loader() -> SkillLoader:
    global _skill_loader
    if _skill_loader is None:
        _skill_loader = SkillLoader()
    return _skill_loader


def set_skill_loader(loader: SkillLoader) -> None:
    """Allow external injection (used by MiniCC during init)."""
    global _skill_loader
    _skill_loader = loader


@tool
def load_skill(name: str) -> str:
    """Load a skill by name to reveal its full instructions and available tools.

    The system prompt lists available skill summaries. After loading, the skill's
    SKILL.md and tool descriptions become visible. Tools can then be called via
    run_skill_tool().
    """
    return _get_loader().load(name)


@tool
def run_skill_tool(skill_name: str, tool_name: str, args: str = "{}") -> str:
    """Execute a tool provided by a loaded skill.

    The skill must be loaded first via load_skill(). *args* is a JSON string
    of arguments as described in the skill's tool documentation.
    """
    try:
        parsed_args = json.loads(args)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON args — {e}"
    return _get_loader().run_tool(skill_name, tool_name, parsed_args)


SKILL_TOOLS = [load_skill, run_skill_tool]
