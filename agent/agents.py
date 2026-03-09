"""ADK agents: coordinator + explore/coder/shell sub-agents with dynamic prompt."""

from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.lite_llm import LiteLlm
from google.genai import types

import config
from managers.skill import SkillLoader
from managers.task import TaskManager
from model import create_adk_model
from tools.base import BASE_TOOLS, READ_ONLY_TOOLS
from tools.code_index import CODE_INDEX_TOOLS
from tools.skill import SKILL_TOOLS
from tools.task import TASK_TOOLS

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
- If a loaded skill explicitly requires the use of todo_write, you MUST strictly follow that requirement without exception.
- Task IDs MUST be sequential integers starting from 1 (e.g. "1", "2", "3"). NEVER use UUIDs, hex strings, or any other format.
{task_status}

## Sub-agent dispatch rules
You can delegate tasks to sub-agents (explore, coder, shell) for isolated execution.
If the user explicitly asks to use a sub-agent, always honor that request.

When to delegate:
- Large-scale code search or exploration → explore
- Isolated coding task (don't pollute current context) → coder
- Run multiple commands and analyze output → shell

When to do it yourself (do NOT delegate):
- Read a single known file → read_file directly
- Make a simple edit → edit_file directly
- Run one command → bash directly

When writing a sub-agent prompt, include all necessary context — the sub-agent cannot see your conversation history.

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
    if not skill_loader.loaded:
        return skill_loader.summaries()
    names = list(skill_loader.loaded.keys())
    if len(names) == 1:
        return f"{names[0]} Skill is already loaded, please obey the skill introduction"
    return f"{', '.join(names)} Skill are already loaded, please obey the skill introduction"


def _estimate_tokens_from_contents(contents: list[types.Content]) -> int:
    """Estimate token count from ADK llm_request.contents (actual LLM context).

    Uses len//4 heuristic; includes text, function_call, function_response.
    """
    total = 0
    for content in contents or []:
        for part in content.parts or []:
            if part.text:
                total += len(part.text) // 4
            if part.function_call:
                total += len(str(part.function_call)) // 4
            if part.function_response:
                total += len(str(part.function_response)) // 4
    return total


def _build_context_status(
    current_tokens: int,
    soft_token_limit: int,
) -> str:
    """Return pressure indicator string for system prompt (same as my-mini-cc)."""
    ratio = current_tokens / max(soft_token_limit, 1)
    if ratio < 0.5:
        return (
            f"[Context: {current_tokens:,}/{soft_token_limit:,} tokens | "
            f"Pressure: LOW]"
        )
    if ratio < 0.8:
        return (
            f"[Context: {current_tokens:,}/{soft_token_limit:,} tokens | "
            f"Pressure: MEDIUM] "
            "Consider delegating exploratory tasks to subagent to slow context growth."
        )
    return (
        f"[Context: {current_tokens:,}/{soft_token_limit:,} tokens | "
        f"Pressure: HIGH] "
        "WARNING: Use subagent for all exploration and search tasks. Avoid verbose output."
    )


def create_inject_dynamic_prompt(
    task_mgr: TaskManager,
    skill_loader: SkillLoader,
):
    """Factory: returns before_model_callback that injects dynamic system prompt."""

    async def inject_dynamic_prompt(
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ):
        # Estimate tokens from llm_request.contents (actual LLM context, more accurate than session.events)
        contents_tokens = _estimate_tokens_from_contents(llm_request.contents)
        base_prompt = SYSTEM_TEMPLATE.format(
            workdir=str(config.settings.workdir),
            skills_summary=_build_skills_summary(skill_loader),
            task_status=task_mgr.render_for_prompt(),
            context_status="",  # placeholder for token estimate
        )
        system_tokens = len(base_prompt) // 4
        total_tokens = contents_tokens + system_tokens
        context_status = _build_context_status(
            total_tokens, config.settings.soft_token_limit
        )
        prompt = SYSTEM_TEMPLATE.format(
            workdir=str(config.settings.workdir),
            skills_summary=_build_skills_summary(skill_loader),
            task_status=task_mgr.render_for_prompt(),
            context_status=context_status,
        )
        llm_request.config.system_instruction = prompt
        return None

    return inject_dynamic_prompt


def _create_sub_agents(model: LiteLlm) -> list[LlmAgent]:
    workdir = str(config.settings.workdir)
    explore = LlmAgent(
        name="explore",
        model=model,
        tools=READ_ONLY_TOOLS,
        instruction=(
            "You are a code explorer. Read-only — never write files. "
            "Search quickly and summarize your findings concisely.\n"
            f"Workspace: {workdir}"
        ),
    )
    coder = LlmAgent(
        name="coder",
        model=model,
        tools=BASE_TOOLS,
        instruction=(
            "You are a coder. Follow the instructions precisely to complete "
            "the coding task. Summarize all changes you made when done.\n"
            f"Workspace: {workdir}"
        ),
    )
    shell = LlmAgent(
        name="shell",
        model=model,
        tools=[BASE_TOOLS[0]],  # bash only
        instruction=(
            "You are a command execution specialist. Run commands and "
            "analyze the results. Provide your conclusions when done.\n"
            f"Workspace: {workdir}"
        ),
    )
    return [explore, coder, shell]


def create_coordinator(
    model: LiteLlm | None = None,
    task_mgr: TaskManager | None = None,
    skill_loader: SkillLoader | None = None,
) -> LlmAgent:
    """Create the main coordinator agent with sub-agents and dynamic prompt."""
    from managers.skill import SkillLoader
    from managers.task import TaskManager

    mdl = model or create_adk_model()
    tm = task_mgr or TaskManager()
    sl = skill_loader or SkillLoader()

    coordinator_tools = BASE_TOOLS + TASK_TOOLS + SKILL_TOOLS + CODE_INDEX_TOOLS
    sub_agents = _create_sub_agents(mdl)

    return LlmAgent(
        name="mini_cc",
        model=mdl,
        tools=coordinator_tools,
        sub_agents=sub_agents,
        instruction="",  # Dynamic via before_model_callback
        before_model_callback=create_inject_dynamic_prompt(tm, sl),
    )
