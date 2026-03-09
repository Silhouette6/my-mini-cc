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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from tenacity import Retrying

import config
from config import Settings, settings as _default_settings
from langchain_core.messages import HumanMessage
from llm import create_llm
from managers.skill import SkillLoader
from managers.task import TaskManager
from memory.summary import AgentMemory
from utils.debug_log import (
    append_model_call_log,
    append_tools_log,
    append_turn_header,
    get_or_create_log_path,
)
from utils.retry import make_retry_kwargs


def _strip_thinking(text: str) -> str:
    """剥离推理模型输出的 <think>...</think> 思考块。

    兼容两种情况：
    - 完整的思考块：<think>...</think>
    - 未闭合的思考块（响应被截断时）：<think>...（无结束标签）
    """
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*$', '', text, flags=re.DOTALL)
    return text.strip()


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

        # Patch global settings so all modules see the overrides
        import config
        config.settings = self._settings

        # Build components
        self._llm = create_llm()
        self._task_mgr = TaskManager(self._settings.tasks_path)
        self._task_mgr.clear()  # Start fresh each run; avoid accumulation across sessions
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

        self._turn_count = 0
        self._log_path: Path | None = None

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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(self, message: str) -> AgentResult:
        """Single-turn interaction. Returns a structured AgentResult."""
        if self._settings.debug_log_enabled:
            return self.chat_with_progress(message, on_status=None)
        self._memory.compress()
        input_messages = self._memory.get_context_messages() + [
            HumanMessage(content=message),
        ]
        result_messages: list = []
        try:
            for attempt in Retrying(**make_retry_kwargs()):
                with attempt:
                    result = self._agent.invoke({"messages": input_messages})
                    result_messages = result.get("messages", [])
        except Exception as e:
            return AgentResult(
                output=f"[错误] API 调用失败（已超出重试次数）：{e}",
                token_usage=self._memory.build_context_status(
                    messages=self._memory.get_context_messages()
                ),
            )

        output = ""
        if result_messages:
            last = result_messages[-1]
            output = last.content if hasattr(last, "content") else str(last)
            output = _strip_thinking(output)

        self._memory.save_messages(result_messages)

        return AgentResult(
            output=output,
            token_usage=self._memory.build_context_status(
                messages=self._memory.get_context_messages()
            ),
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
        messages = self._memory.get_context_messages() + [
            HumanMessage(content=message),
        ]
        input_messages = list(messages)

        if self._settings.debug_log_enabled:
            self._turn_count += 1
            self._log_path = get_or_create_log_path(
                self._settings.workdir,
                self._settings.debug_log_dir,
                self._log_path,
            )
            append_turn_header(self._log_path, self._turn_count, message)

        # messages 在每次 Retrying attempt 开始时重置为 input_messages，
        # 防止因中途失败导致消息列表处于不一致的中间状态。
        messages = list(input_messages)
        model_iter = 0

        try:
            for attempt in Retrying(**make_retry_kwargs()):
                with attempt:
                    # 重置本次 attempt 的消息列表和 debug 计数器
                    messages = list(input_messages)
                    model_iter = 0

                    for chunk in self._agent.stream({"messages": messages}):
                        for node_name, node_output in chunk.items():
                            if node_name == "__metadata__":
                                continue
                            if not isinstance(node_output, dict):
                                continue
                            msgs = node_output.get("messages", [])

                            if self._settings.debug_log_enabled and msgs:
                                if node_name == "model":
                                    model_iter += 1
                                    append_model_call_log(
                                        self._log_path,
                                        model_iter,
                                        self._get_system_prompt(messages=messages),
                                        messages,
                                        msgs,
                                    )
                                elif node_name == "tools":
                                    append_tools_log(self._log_path, model_iter, msgs)

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
                                    content = getattr(msg, "content", None) or ""
                                    content = str(content).strip()
                                    max_len = config.settings.progress_status_tool_result_max
                                    if content:
                                        preview = content[:max_len] + ("..." if len(content) > max_len else "")
                                        on_status(f"Result: {preview}")
                                    else:
                                        on_status("Result: (empty)")
                                    break
        except Exception as e:
            return AgentResult(
                output=f"[错误] API 调用失败（已超出重试次数）：{e}",
                token_usage=self._memory.build_context_status(
                    messages=self._memory.get_context_messages()
                ),
            )

        output = ""
        if messages:
            last = messages[-1]
            output = last.content if hasattr(last, "content") else str(last)
            output = _strip_thinking(output)

        self._memory.save_messages(messages)

        return AgentResult(
            output=output,
            token_usage=self._memory.build_context_status(
                messages=self._memory.get_context_messages()
            ),
        )

    def stream(self, message: str) -> Iterator[str]:
        """Streaming interaction — yields text chunks.

        注意：generator 内部无法使用 Retrying 上下文管理器（yield 不能在
        with 块内跨越 attempt 边界），因此此方法仅做最外层 try/except 兜底。
        429 / Timeout 的重试保护由 LLM 层（memory/summary.py）覆盖；
        如需在 stream 级别重试，建议改用 chat_with_progress()。
        """
        self._memory.compress()
        messages = self._memory.get_context_messages() + [
            HumanMessage(content=message),
        ]
        full_output = ""
        model_iter = 0

        if self._settings.debug_log_enabled:
            self._turn_count += 1
            self._log_path = get_or_create_log_path(
                self._settings.workdir,
                self._settings.debug_log_dir,
                self._log_path,
            )
            append_turn_header(self._log_path, self._turn_count, message)

        try:
            for chunk in self._agent.stream({"messages": messages}):
                for node_name, node_output in chunk.items():
                    if node_name == "__metadata__":
                        continue
                    if not isinstance(node_output, dict):
                        continue
                    msgs = node_output.get("messages", [])

                    if self._settings.debug_log_enabled and msgs:
                        if node_name == "model":
                            model_iter += 1
                            append_model_call_log(
                                self._log_path,
                                model_iter,
                                self._get_system_prompt(messages=messages),
                                messages,
                                msgs,
                            )
                        elif node_name == "tools":
                            append_tools_log(self._log_path, model_iter, msgs)

                    if msgs:
                        messages = messages + msgs
                    for msg in msgs:
                        content = msg.content if hasattr(msg, "content") else ""
                        if content and not hasattr(msg, "tool_calls"):
                            yield content
                            full_output += content
        except Exception as e:
            yield f"[错误] API 调用失败：{e}"

        self._memory.save_messages(messages)

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
        """Clear all memory, tasks, and message history, start fresh."""
        self._memory.clear()
        self._task_mgr.clear()

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
