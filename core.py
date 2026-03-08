"""MiniCC — the single programmatic API (Facade) for the agent system.

Usage::

    from core import MiniCC

    agent = MiniCC()
    result = agent.chat("Analyze the project structure")
    print(result.output)

    # Or one-shot:
    output = MiniCC.quick_run("List all TODO comments in src/", workdir="/project")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from config import Settings, settings as _default_settings
from llm import create_llm
from managers.skill import SkillLoader
from managers.task import TaskManager
from memory.summary import AgentMemory


def _format_tool_args(tool_name: str, args: dict | None) -> str:
    """Format tool name + key args for status display."""
    import config
    s = config.settings
    if not args or not isinstance(args, dict):
        return tool_name
    if tool_name == "bash" and "command" in args:
        cmd = str(args["command"]).strip()
        m = s.progress_status_bash_max
        return f"{tool_name}: {cmd[:m]}{'...' if len(cmd) > m else ''}"
    if tool_name == "read_file":
        path = args.get("path", args.get("file", ""))
        m = s.progress_status_read_file_max
        return f"{tool_name}: {str(path)[:m]}{'...' if len(str(path)) > m else ''}"
    if tool_name in ("edit_file", "write_file"):
        path = args.get("path", "")
        m = s.progress_status_edit_path_max
        return f"{tool_name}: {str(path)[:m]}{'...' if len(str(path)) > m else ''}"
    # Generic: show first arg value
    m = s.progress_status_generic_max
    for k, v in args.items():
        val = str(v)
        return f"{tool_name}: {val[:m]}{'...' if len(val) > m else ''}"
    return tool_name


@dataclass
class AgentResult:
    """Structured result from a single chat turn."""
    output: str
    tools_used: list[str] = field(default_factory=list)
    token_usage: str = ""


class MiniCC:
    """Facade that assembles all internal components and exposes a clean API."""

    def __init__(
        self,
        workdir: str | Path | None = None,
        llm_provider: str | None = None,
        model_id: str | None = None,
        api_base_url: str | None = None,
        soft_token_limit: int | None = None,
        hard_token_limit: int | None = None,
    ):
        overrides: dict[str, Any] = {}
        if workdir is not None:
            overrides["workdir"] = Path(workdir)
        if llm_provider is not None:
            overrides["llm_provider"] = llm_provider
        if model_id is not None:
            overrides["model_id"] = model_id
        if api_base_url is not None:
            overrides["api_base_url"] = api_base_url
        if soft_token_limit is not None:
            overrides["soft_token_limit"] = soft_token_limit
        if hard_token_limit is not None:
            overrides["hard_token_limit"] = hard_token_limit

        if overrides:
            self._settings = Settings(**{
                **_default_settings.model_dump(),
                **overrides,
            })
        else:
            self._settings = _default_settings

        # Patch global settings so all modules see the overrides
        import config
        config.settings = self._settings

        # Build components
        self._llm = create_llm()
        self._task_mgr = TaskManager(self._settings.tasks_path)
        self._skill_loader = SkillLoader(self._settings.skills_path)
        self._memory = AgentMemory(
            llm=self._llm,
            soft_token_limit=self._settings.soft_token_limit,
            hard_token_limit=self._settings.hard_token_limit,
            transcript_dir=self._settings.transcript_path,
        )

        # Inject managers into tool modules
        from tools.task import set_task_manager
        from tools.skill import set_skill_loader
        set_task_manager(self._task_mgr)
        set_skill_loader(self._skill_loader)

        # Build agent graph
        from tools import get_all_tools
        from agent.executor import build_agent

        all_tools = get_all_tools(include_subagent=True)
        self._agent, self._get_system_prompt = build_agent(
            llm=self._llm,
            tools=all_tools,
            memory=self._memory,
            task_mgr=self._task_mgr,
            skill_loader=self._skill_loader,
        )

        # Persistent message history for multi-turn conversations
        self._messages: list = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, message: str) -> AgentResult:
        """Single-turn interaction. Returns a structured AgentResult."""
        # Run memory compression pipeline before each turn
        self._memory.compress()

        # Build input with current system prompt + user message
        self._messages.append({"role": "user", "content": message})

        # Invoke the agent graph
        result = self._agent.invoke(
            {"messages": self._messages},
        )

        # Extract output from the last AI message
        messages = result.get("messages", [])
        output = ""
        if messages:
            last = messages[-1]
            output = last.content if hasattr(last, "content") else str(last)

        # Update conversation state
        self._messages = list(messages)

        # Save to memory for compression tracking
        self._memory.save_turn(message, output)

        return AgentResult(
            output=output,
            token_usage=self._memory.build_context_status(messages=self._messages),
        )

    def chat_with_progress(
        self,
        message: str,
        on_status: Callable[[str], None] | None = None,
    ) -> AgentResult:
        """Single-turn interaction with progress callback for terminal visualization.

        Calls on_status(status_str) when model is thinking or tools are running.
        """
        self._memory.compress()
        self._messages.append({"role": "user", "content": message})

        messages = list(self._messages)
        for chunk in self._agent.stream({"messages": self._messages}):
            for node_name, node_output in chunk.items():
                if node_name == "__metadata__":
                    continue
                if not isinstance(node_output, dict):
                    continue
                msgs = node_output.get("messages", [])
                if msgs:
                    messages = messages + msgs

                if on_status is None:
                    continue

                if node_name == "model":
                    for msg in msgs:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            parts = []
                            for tc in msg.tool_calls:
                                name = tc.get("name", "?") if isinstance(tc, dict) else getattr(tc, "name", "?")
                                raw = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", None)
                                args = raw if isinstance(raw, dict) else {}
                                detail = _format_tool_args(name, args)
                                parts.append(detail)
                            on_status(f"Calling: {' | '.join(parts)}")
                        else:
                            on_status("Thinking...")
                        break
                elif node_name == "tools":
                    for msg in msgs:
                        name = getattr(msg, "name", None) or "tool"
                        on_status(f"Tool: {name}")
                        break

        output = ""
        if messages:
            last = messages[-1]
            output = last.content if hasattr(last, "content") else str(last)

        self._messages = list(messages)
        self._memory.save_turn(message, output)

        return AgentResult(
            output=output,
            token_usage=self._memory.build_context_status(messages=self._messages),
        )

    def stream(self, message: str) -> Iterator[str]:
        """Streaming interaction — yields text chunks."""
        self._messages.append({"role": "user", "content": message})
        full_output = ""

        for chunk in self._agent.stream(
            {"messages": self._messages},
        ):
            # LangGraph streams dicts with node output
            for node_output in chunk.values():
                msgs = node_output.get("messages", [])
                for msg in msgs:
                    content = msg.content if hasattr(msg, "content") else ""
                    if content and not hasattr(msg, "tool_calls"):
                        yield content
                        full_output += content

        # Update state after streaming completes
        result = self._agent.invoke({"messages": self._messages})
        self._messages = list(result.get("messages", []))
        self._memory.save_turn(message, full_output)

    @classmethod
    def quick_run(
        cls,
        prompt: str,
        workdir: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Stateless one-shot call. Creates a temporary MiniCC, runs one turn."""
        agent = cls(workdir=workdir, **kwargs)
        result = agent.chat(prompt)
        return result.output

    def compact(self) -> None:
        """Manually trigger memory compression (Layer 3 hard compact)."""
        self._memory.compact()

    def reset(self) -> None:
        """Clear all memory and message history, start fresh."""
        self._memory.clear()
        self._messages = []

    # ------------------------------------------------------------------
    # Component access
    # ------------------------------------------------------------------

    @property
    def tasks(self) -> TaskManager:
        return self._task_mgr

    @property
    def skills(self) -> SkillLoader:
        return self._skill_loader

    @property
    def memory(self) -> AgentMemory:
        return self._memory
