"""Main agent builder — dynamic system prompt with task status and context pressure.

Uses langchain.agents.create_agent (LangChain v1.2+) which returns a compiled
LangGraph StateGraph with a built-in tool-calling loop.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents import create_agent
from langchain.agents.middleware.types import dynamic_prompt, wrap_model_call
from langchain_core.language_models import BaseChatModel

import config
from managers.skill import SkillLoader
from managers.task import TaskManager
from memory.summary import AgentMemory

SYSTEM_TEMPLATE = """\
You are a coding agent working at {workdir}. Use tools to solve tasks.
Always respond to the user in Chinese (Simplified).

## Available skills
{skills_summary}

## Task management
You MUST use the task board (todo_write) for multi-step work:
- When you receive a task with 2+ steps, ALWAYS start by calling todo_write to create a task list before executing.
- Break complex tasks into subtasks with clear dependencies (blocked_by). Mark one task in_progress at a time.
- After completing each step, update its status to completed immediately.
- todo_write returns "OK. N/M completed" only. Current task board is always shown below (refreshed before each model call).
{task_status}

## Sub-agent dispatch rules
You can use the **subagent** tool to spawn a temporary Worker for isolated tasks.
If the user explicitly asks to use subagent, always honor that request.

When to spawn a Worker:
- Large-scale code search or exploration → worker_type="explore"
- Isolated coding task (don't pollute current context) → worker_type="coder"
- Run multiple commands and analyze output → worker_type="shell"

When to do it yourself (do NOT spawn):
- Read a single known file → read_file directly
- Make a simple edit → edit_file directly
- Run one command → bash directly

When writing a Worker prompt, include all necessary context — the Worker cannot \
see your conversation history.

## Code index tool (prefer over read_file for symbols)
get_symbol_body(file_path, symbol_name): Get ONLY the code of a function/method/class. \
Prefer this over read_file when you need a specific symbol — saves tokens. Use when user \
asks "where is X defined", "show me Y's implementation", or when inspecting a known symbol. \
First call builds index (may take minutes); later calls use cache. If not found, returns \
available_symbols. Flow: bash/grep to find files → get_symbol_body to fetch implementation.

## Context status
{context_status}
"""


def _build_skills_summary(skill_loader: SkillLoader) -> str:
    """Return skill summaries or loaded-skill hint based on load state."""
    if not skill_loader.loaded:
        return skill_loader.summaries()
    names = list(skill_loader.loaded.keys())
    if len(names) == 1:
        return f"{names[0]} Skill is already loaded, please obey the skill introduction"
    return f"{', '.join(names)} Skill are already loaded, please obey the skill introduction"


def _build_system_prompt(
    task_mgr: TaskManager,
    skill_loader: SkillLoader,
    memory: AgentMemory,
    messages: list | None = None,
) -> str:
    """Build system prompt. Pass messages for accurate context pressure (e.g. from ModelRequest.messages)."""
    return SYSTEM_TEMPLATE.format(
        workdir=str(config.settings.workdir),
        skills_summary=_build_skills_summary(skill_loader),
        task_status=task_mgr.render_for_prompt(),
        context_status=memory.build_context_status(messages),
    )


def build_agent(
    llm: BaseChatModel,
    tools: list,
    memory: AgentMemory,
    task_mgr: TaskManager,
    skill_loader: SkillLoader,
) -> tuple[Any, Callable[[], str]]:
    """Build the main agent graph with dynamic system prompt via middleware.

    Returns:
        (agent_graph, get_system_prompt) — get_system_prompt() returns latest
        prompt; middleware injects it before each model call.
    """

    @wrap_model_call
    def compress_before_model(request: Any, handler: Any) -> Any:
        compressed = memory.compress_messages(request.messages)
        return handler(request.override(messages=compressed))

    @dynamic_prompt
    def get_system_prompt(request: Any) -> str:
        msgs = getattr(request, "messages", None) if request is not None else None
        return _build_system_prompt(task_mgr, skill_loader, memory, messages=msgs)

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=None,
        middleware=[compress_before_model, get_system_prompt],
        name="mini-cc",
    )

    def _get_system_prompt(messages: list | None = None) -> str:
        return _build_system_prompt(task_mgr, skill_loader, memory, messages=messages)

    return agent, _get_system_prompt
