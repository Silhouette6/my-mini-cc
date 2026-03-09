"""MiniCC — ADK-based facade for the agent system."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from google.adk.apps.app import App
from google.adk.apps.app import EventsCompactionConfig
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.genai import types

import config
from config import Settings, settings as _default_settings
from agent.agents import create_coordinator
from managers.skill import SkillLoader
from managers.task import TaskManager
from tools.skill import set_skill_loader
from tools.task import set_task_manager


def _clear_startup_caches(workdir: Path) -> None:
    """Clear caches on program startup: tasks (already done), code index cache."""
    try:
        from code_index_mcp.project_manager_cache import get_manager_cache
        path_key = str(workdir.resolve())
        get_manager_cache().clear_project(path_key)
    except ImportError:
        pass  # code_index_mcp not installed


def _strip_thinking(text: str) -> str:
    """Strip <think>...</think> blocks from model output."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL)
    return text.strip()


def _format_tool_args(tool_name: str, args: dict | None) -> str:
    """Format tool name + key args for status display."""
    s = config.settings
    if not args or not isinstance(args, dict):
        return tool_name
    if tool_name == "bash" and "command" in args:
        cmd = str(args["command"]).strip()
        m = s.progress_status_bash_max
        return f"{tool_name}: {cmd[:m]}{'...' if len(cmd) > m else ''}"
    if tool_name == "read_file":
        path = args.get("path", args.get("file", ""))
        sl = args.get("start_line")
        el = args.get("end_line")
        suffix = f":{sl}-{el}" if sl is not None and el is not None else ""
        m = s.progress_status_read_file_max
        part = f"{path}{suffix}"
        return f"{tool_name}: {str(part)[:m]}{'...' if len(str(part)) > m else ''}"
    if tool_name in ("edit_file", "write_file"):
        path = args.get("path", "")
        m = s.progress_status_edit_path_max
        return f"{tool_name}: {str(path)[:m]}{'...' if len(str(path)) > m else ''}"
    if tool_name == "get_symbol_body":
        fp = args.get("file_path", "")
        sym = args.get("symbol_name", "")
        m = s.progress_status_generic_max
        part = f"{fp}::{sym}" if fp and sym else fp or sym
        return f"{tool_name}: {str(part)[:m]}{'...' if len(str(part)) > m else ''}"
    if tool_name == "todo_write":
        val = args.get("items", args.get("merge", ""))
        m = s.progress_status_task_max
        part = str(val)[:m]
        return f"{tool_name}: {part}{'...' if len(str(val)) > m else ''}"
    m = s.progress_status_generic_max
    for k, v in args.items():
        val = str(v)
        return f"{tool_name}: {val[:m]}{'...' if len(val) > m else ''}"
    return tool_name


def _get_text_from_content(content: types.Content | None) -> str:
    """Extract text from content.parts."""
    if not content or not content.parts:
        return ""
    out = []
    for p in content.parts:
        if hasattr(p, "text") and p.text:
            out.append(p.text)
    return "".join(out)


@dataclass
class AgentResult:
    """Structured result from a single chat turn."""
    output: str
    tools_used: list[str] = field(default_factory=list)
    token_usage: str = ""


class MiniCC:
    """Facade that assembles ADK components and exposes a clean API."""

    def __init__(
        self,
        workdir: str | Path | None = None,
        llm_provider: str | None = None,
        model_id: str | None = None,
        api_base_url: str | None = None,
        soft_token_limit: int | None = None,
        hard_token_limit: int | None = None,
        debug_log_enabled: bool | None = None,
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
        if debug_log_enabled is not None:
            overrides["debug_log_enabled"] = debug_log_enabled

        if overrides:
            self._settings = Settings(**{
                **_default_settings.model_dump(),
                **overrides,
            })
        else:
            self._settings = _default_settings

        import config
        config.settings = self._settings

        self._task_mgr = TaskManager(self._settings.tasks_path)
        self._task_mgr.clear()
        _clear_startup_caches(self._settings.workdir)
        self._skill_loader = SkillLoader(self._settings.skills_path)

        set_task_manager(self._task_mgr)
        set_skill_loader(self._skill_loader)

        self._coordinator = create_coordinator(
            task_mgr=self._task_mgr,
            skill_loader=self._skill_loader,
        )

        compaction_config = EventsCompactionConfig(
            compaction_interval=3,
            overlap_size=1,
        )

        self._app = App(
            name="mini_cc",
            root_agent=self._coordinator,
            events_compaction_config=compaction_config,
        )

        self._runner = Runner(
            app=self._app,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            auto_create_session=True,
        )

        self._user_id = "default"
        self._session_id = "default"

    def chat(self, message: str) -> AgentResult:
        """Single-turn interaction. Returns a structured AgentResult."""
        if self._settings.debug_log_enabled:
            return self.chat_with_progress(message, on_status=None)
        return asyncio.run(self._chat_impl(message, on_status=None))

    def chat_with_progress(
        self,
        message: str,
        on_status: Callable[[str], None] | None = None,
    ) -> AgentResult:
        """Single-turn interaction with progress callback."""
        return asyncio.run(self._chat_impl(message, on_status=on_status))

    async def _chat_impl(
        self,
        message: str,
        on_status: Callable[[str], None] | None = None,
    ) -> AgentResult:
        new_message = types.Content(
            role="user",
            parts=[types.Part.from_text(text=message)],
        )
        output = ""
        tools_used: list[str] = []

        async for event in self._runner.run_async(
            user_id=self._user_id,
            session_id=self._session_id,
            new_message=new_message,
        ):
            if on_status:
                if event.get_function_calls():
                    parts = []
                    for fc in event.get_function_calls():
                        name = getattr(fc, "name", "?")
                        args = getattr(fc, "args", None) or {}
                        detail = _format_tool_args(name, args)
                        parts.append(detail)
                    on_status(f"Calling: {' | '.join(parts)}")
                elif event.get_function_responses():
                    on_status("Tool: result")
                elif not event.partial and event.content and event.content.parts:
                    on_status("Thinking...")

            if event.get_function_calls():
                for fc in event.get_function_calls():
                    tools_used.append(getattr(fc, "name", "?"))

            if event.is_final_response() and event.content:
                output = _get_text_from_content(event.content)

        return AgentResult(
            output=_strip_thinking(output),
            tools_used=tools_used,
        )

    def stream(self, message: str) -> Iterator[str]:
        """Streaming interaction — yields text chunks."""
        result = self.chat(message)
        if result.output:
            yield result.output

    @classmethod
    def quick_run(
        cls,
        prompt: str,
        workdir: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Stateless one-shot call."""
        agent = cls(workdir=workdir, **kwargs)
        result = agent.chat(prompt)
        return result.output

    def compact(self) -> None:
        """Placeholder: ADK handles compaction via EventsCompactionConfig."""
        pass

    def reset(self) -> None:
        """Clear tasks, memory, and caches."""
        self._task_mgr.clear()
        _clear_startup_caches(self._settings.workdir)
        self._runner = Runner(
            app=self._app,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            auto_create_session=True,
        )

    @property
    def tasks(self) -> TaskManager:
        return self._task_mgr

    @property
    def skills(self) -> SkillLoader:
        return self._skill_loader
