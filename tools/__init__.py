"""Aggregate all tools for the main agent."""

from tools.base import BASE_TOOLS
from tools.skill import SKILL_TOOLS
from tools.task import TASK_TOOLS


def get_all_tools(include_subagent: bool = True) -> list:
    """Return the complete tool list for the main AgentExecutor.

    When *include_subagent* is True the subagent tool is appended
    (import deferred to avoid circular dependency).
    """
    tools = list(BASE_TOOLS) + list(TASK_TOOLS) + list(SKILL_TOOLS)

    try:
        from tools.code_index import CODE_INDEX_TOOLS
        tools.extend(CODE_INDEX_TOOLS)
    except ImportError:
        pass

    if include_subagent:
        from agent.subagent import subagent
        tools.append(subagent)
    return tools
