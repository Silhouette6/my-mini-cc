"""Three-layer AgentMemory:

Layer 1 — microcompact : Clear old tool-result contents (zero LLM cost).
Layer 2 — progressive summary : Incrementally summarize oldest messages when
           tokens exceed *soft_token_limit* (inspired by
           ConversationSummaryBufferMemory).
Layer 3 — hard compact : Full summary + transcript dump when tokens exceed
           *hard_token_limit* or on manual /compact.

Also exposes *build_context_status()* for dynamic pressure injection into the
system prompt, forming a self-regulating loop: high pressure → agent picks
subagent → only summary returned → context growth slows → pressure eases.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

import config
from utils.retry import with_llm_retry


class AgentMemory:
    """Three-layer compression memory with context-pressure awareness."""

    def __init__(
        self,
        llm: BaseChatModel,
        soft_token_limit: int | None = None,
        hard_token_limit: int | None = None,
        transcript_dir: Path | None = None,
    ):
        self.llm = llm
        self.soft_token_limit = soft_token_limit or config.settings.soft_token_limit
        self.hard_token_limit = hard_token_limit or config.settings.hard_token_limit
        self.transcript_dir = transcript_dir or config.settings.transcript_path

        self.buffer: list[BaseMessage] = []
        self.moving_summary: str = ""

    # ------------------------------------------------------------------
    # Main entry point — run compression pipeline
    # ------------------------------------------------------------------

    def compress(self) -> None:
        """Run the full three-layer compression pipeline."""
        # Layer 1: microcompact (every turn, zero LLM cost)
        self._microcompact()

        # Layer 2: progressive summary (trim oldest turn while over soft limit)
        while (
            self._estimate_tokens() > self.soft_token_limit
            and self._get_first_turn_end() < len(self.buffer)
        ):
            self._prune_oldest_to_summary()

        # Layer 3: hard compact (emergency, if still over hard limit)
        if self._estimate_tokens() > self.hard_token_limit:
            self._hard_compact()

    def get_context_messages(self) -> list[BaseMessage]:
        """Return the current context: optional summary + recent buffer."""
        messages: list[BaseMessage] = []
        if self.moving_summary:
            messages.append(
                SystemMessage(
                    content=f"<context_summary>\n{self.moving_summary}\n</context_summary>"
                )
            )
        messages.extend(self.buffer)
        return messages

    def save_turn(self, user_input: str, ai_output: str) -> None:
        """Record a completed turn into the buffer (compat)."""
        msgs: list[BaseMessage] = []
        if user_input:
            msgs.append(HumanMessage(content=user_input))
        if ai_output:
            msgs.append(AIMessage(content=ai_output))
        if msgs:
            self.buffer.extend(msgs)

    def save_messages(self, messages: list) -> None:
        """Replace buffer with full conversation (Human, AI, ToolMessage)."""
        self.buffer = self._normalize_messages(messages)

    def _normalize_messages(self, messages: list) -> list[BaseMessage]:
        """Convert dict or BaseMessage to BaseMessage list."""
        out: list[BaseMessage] = []
        for m in messages:
            if isinstance(m, BaseMessage):
                out.append(m)
            elif isinstance(m, dict):
                role = m.get("role", "")
                content = m.get("content", "")
                if role == "user":
                    out.append(HumanMessage(content=content))
                elif role == "assistant":
                    out.append(AIMessage(content=content))
        return out

    def clear(self) -> None:
        """Reset all memory."""
        self.buffer = []
        self.moving_summary = ""

    # ------------------------------------------------------------------
    # Layer 1: microcompact
    # ------------------------------------------------------------------

    def _microcompact(self) -> None:
        """Clear verbose content from old tool messages.

        Keeps the N most recent tool messages per type:
        - Regular tools: memory_tool_retain (default 5)
        - Subagent returns: memory_subagent_retain (default 10) — more important
        - load_skill returns: memory_load_skill_retain (default 10)
        """
        tool_retain = config.settings.memory_tool_retain
        subagent_retain = config.settings.memory_subagent_retain
        load_skill_retain = config.settings.memory_load_skill_retain

        tool_indices: list[int] = []
        subagent_indices: list[int] = []
        load_skill_indices: list[int] = []
        for i, msg in enumerate(self.buffer):
            if not self._is_tool_message(msg):
                continue
            tool_indices.append(i)
            if getattr(msg, "name", None) == "subagent":
                subagent_indices.append(i)
            elif getattr(msg, "name", None) == "load_skill":
                load_skill_indices.append(i)

        special_indices = set(subagent_indices) | set(load_skill_indices)
        regular_indices = [i for i in tool_indices if i not in special_indices]

        def clear_at(idx: int) -> None:
            msg = self.buffer[idx]
            if not isinstance(msg.content, str) or len(msg.content) <= 100:
                return
            if isinstance(msg, ToolMessage):
                self.buffer[idx] = ToolMessage(
                    content="[cleared]",
                    tool_call_id=msg.tool_call_id,
                    name=getattr(msg, "name", None),
                )
            else:
                self.buffer[idx] = type(msg)(content="[cleared]")

        for idx in regular_indices[:-tool_retain]:
            clear_at(idx)
        for idx in subagent_indices[:-subagent_retain]:
            clear_at(idx)
        for idx in load_skill_indices[:-load_skill_retain]:
            clear_at(idx)

    def compress_messages(self, messages: list) -> list[BaseMessage]:
        """Apply Layer 1 style compression to a message list. Returns new list, does not mutate input.

        Used before each model call to avoid context explosion within a turn.
        Keeps the N most recent tool messages per type; older ones get content cleared to [cleared].
        """
        tool_retain = config.settings.memory_tool_retain
        subagent_retain = config.settings.memory_subagent_retain
        load_skill_retain = config.settings.memory_load_skill_retain

        tool_indices: list[int] = []
        subagent_indices: list[int] = []
        load_skill_indices: list[int] = []
        for i, msg in enumerate(messages):
            if not self._is_tool_message(msg):
                continue
            tool_indices.append(i)
            if getattr(msg, "name", None) == "subagent":
                subagent_indices.append(i)
            elif getattr(msg, "name", None) == "load_skill":
                load_skill_indices.append(i)

        special_indices = set(subagent_indices) | set(load_skill_indices)
        regular_indices = [i for i in tool_indices if i not in special_indices]

        to_clear = set(regular_indices[:-tool_retain] if tool_retain else [])
        to_clear |= set(subagent_indices[:-subagent_retain] if subagent_retain else [])
        to_clear |= set(load_skill_indices[:-load_skill_retain] if load_skill_retain else [])

        result: list[BaseMessage] = []
        for i, msg in enumerate(messages):
            if i not in to_clear:
                result.append(msg)
                continue
            if not self._is_tool_message(msg) or not isinstance(msg.content, str) or len(msg.content) <= 100:
                result.append(msg)
                continue
            if isinstance(msg, ToolMessage):
                result.append(
                    ToolMessage(
                        content="[cleared]",
                        tool_call_id=msg.tool_call_id,
                        name=getattr(msg, "name", None),
                    )
                )
            else:
                result.append(type(msg)(content="[cleared]"))
        return result

    @staticmethod
    def _is_tool_message(msg: BaseMessage) -> bool:
        return msg.type in ("tool", "function") or (
            hasattr(msg, "additional_kwargs")
            and "tool_call_id" in msg.additional_kwargs
        )

    # ------------------------------------------------------------------
    # Layer 2: progressive summary (inspired by SummaryBufferMemory)
    # ------------------------------------------------------------------

    def _get_first_turn_end(self) -> int:
        """Return exclusive end index of the first full user turn."""
        human_count = 0
        for i, msg in enumerate(self.buffer):
            if getattr(msg, "type", "") == "human":
                human_count += 1
                if human_count == 2:
                    return i
        return len(self.buffer)

    def _prune_oldest_to_summary(self) -> None:
        """Move the oldest full user turn into the running summary."""
        end = self._get_first_turn_end()
        if end < 1 or end >= len(self.buffer):
            return
        oldest_turn = self.buffer[:end]
        self.buffer = self.buffer[end:]

        new_content = "\n".join(
            f"[{m.type}] {str(m.content or '')[:500]}" for m in oldest_turn if m.content
        )
        prompt = (
            "Progressively summarize the conversation, integrating the new turn "
            "into the existing summary. Return ONLY the updated summary, keep it "
            "concise.\n\n"
            f"Existing summary:\n{self.moving_summary or '(empty)'}\n\n"
            f"New turn:\n{new_content}\n\n"
            "Updated summary:"
        )
        response = with_llm_retry(self.llm.invoke, prompt)
        self.moving_summary = (
            response.content if hasattr(response, "content") else str(response)
        )

    # ------------------------------------------------------------------
    # Layer 3: hard compact
    # ------------------------------------------------------------------

    def _hard_compact(self) -> None:
        """Emergency full compression + transcript dump."""
        self._save_transcript()
        full_context = f"Summary so far:\n{self.moving_summary}\n\nRecent messages:\n"
        full_context += "\n".join(
            f"[{m.type}] {m.content[:300]}" for m in self.buffer if m.content
        )
        full_context = full_context[:80000]

        response = with_llm_retry(
            self.llm.invoke,
            f"Summarize this entire conversation for continuity. "
            f"Be concise but preserve key decisions and context:\n{full_context}",
        )
        self.moving_summary = (
            response.content if hasattr(response, "content") else str(response)
        )
        self.buffer = []

    def _save_transcript(self) -> None:
        """Persist current conversation to a JSONL file."""
        self.transcript_dir.mkdir(parents=True, exist_ok=True)
        path = self.transcript_dir / f"transcript_{int(time.time())}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            if self.moving_summary:
                f.write(
                    json.dumps({"summary": self.moving_summary}, ensure_ascii=False)
                    + "\n"
                )
            for msg in self.buffer:
                f.write(
                    json.dumps(
                        {"role": msg.type, "content": msg.content},
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def compact(self) -> None:
        """Manual /compact trigger — forces Layer 3."""
        self._hard_compact()

    def _estimate_tokens(self, messages: list | None = None) -> int:
        """Estimate token count. If messages given, use those; else use buffer."""
        total = len(self.moving_summary) // 4
        for msg in (messages if messages is not None else self.buffer):
            content = getattr(msg, "content", None)
            content = content if isinstance(content, str) else str(content or "")
            total += len(content) // 4
            # Include tool_calls (AIMessage) for accuracy
            tc = getattr(msg, "tool_calls", None)
            if tc:
                total += len(str(tc)) // 4
        return total

    def build_context_status(self, messages: list | None = None) -> str:
        """Return a pressure indicator string for injection into the system prompt.

        If *messages* is provided (e.g. agent's full message list), estimate from that
        to reflect actual LLM context. Otherwise use buffer (simplified view).
        """
        current = self._estimate_tokens(messages)
        ratio = current / max(self.soft_token_limit, 1)
        if ratio < 0.5:
            return (
                f"[Context: {current:,}/{self.soft_token_limit:,} tokens | "
                f"Pressure: LOW]"
            )
        if ratio < 0.8:
            return (
                f"[Context: {current:,}/{self.soft_token_limit:,} tokens | "
                f"Pressure: MEDIUM] "
                "Consider delegating exploratory tasks to subagent to slow context growth."
            )
        return (
            f"[Context: {current:,}/{self.soft_token_limit:,} tokens | "
                f"Pressure: HIGH] "
            "WARNING: Use subagent for all exploration and search tasks. Avoid verbose output."
        )
