"""Main agent builder — dynamic system prompt with task status and context pressure.

Uses langchain.agents.create_agent (LangChain v1.2+) which returns a compiled
LangGraph StateGraph with a built-in tool-calling loop.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage

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
You MUST use the task board (todo_write / todo_list) for multi-step work:
- When you receive a task with 2+ steps, ALWAYS start by calling todo_write to create a task list before executing.
- Break complex tasks into subtasks with clear dependencies (blocked_by). Mark one task in_progress at a time.
- After completing each step, update its status to completed immediately.
- Call todo_list before starting work to see what's already tracked; avoid duplicating work.
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

## Context status
{context_status}
"""


def _build_system_prompt(
    task_mgr: TaskManager,
    skill_loader: SkillLoader,
    memory: AgentMemory,
) -> str:
    return SYSTEM_TEMPLATE.format(
        workdir=str(config.settings.workdir),
        skills_summary=skill_loader.summaries(),
        task_status=task_mgr.render_for_prompt(),
        context_status=memory.build_context_status(),
    )


def build_agent(
    llm: BaseChatModel,
    tools: list,
    memory: AgentMemory,
    task_mgr: TaskManager,
    skill_loader: SkillLoader,
) -> tuple[Any, Callable[[], str]]:
    """Build the main agent graph and return it with a system-prompt factory.

    Returns:
        (agent_graph, get_system_prompt) — call get_system_prompt() before each
        invocation to get the latest dynamic system prompt.
    """
    initial_prompt = _build_system_prompt(task_mgr, skill_loader, memory)

    agent = create_agent(
        model=llm,
        tools=tools,
        system_prompt=initial_prompt,
        name="mini-cc",
    )

    def get_system_prompt() -> str:
        return _build_system_prompt(task_mgr, skill_loader, memory)

    return agent, get_system_prompt
