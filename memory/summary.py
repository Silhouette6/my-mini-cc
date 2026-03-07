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
)

import config


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

        # Layer 2: progressive summary (trim oldest while over soft limit)
        while (
            self._estimate_tokens() > self.soft_token_limit
            and len(self.buffer) > 4
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
        """Record a completed turn into the buffer."""
        if user_input:
            self.buffer.append(HumanMessage(content=user_input))
        if ai_output:
            self.buffer.append(AIMessage(content=ai_output))

    def clear(self) -> None:
        """Reset all memory."""
        self.buffer = []
        self.moving_summary = ""

    # ------------------------------------------------------------------
    # Layer 1: microcompact
    # ------------------------------------------------------------------

    def _microcompact(self) -> None:
        """Clear verbose content from all but the 3 most recent tool messages."""
        tool_indices: list[int] = []
        for i, msg in enumerate(self.buffer):
            if self._is_tool_message(msg):
                tool_indices.append(i)
        if len(tool_indices) <= 3:
            return
        for idx in tool_indices[:-3]:
            msg = self.buffer[idx]
            if isinstance(msg.content, str) and len(msg.content) > 100:
                self.buffer[idx] = type(msg)(content="[cleared]")

    @staticmethod
    def _is_tool_message(msg: BaseMessage) -> bool:
        return msg.type in ("tool", "function") or (
            hasattr(msg, "additional_kwargs")
            and "tool_call_id" in msg.additional_kwargs
        )

    # ------------------------------------------------------------------
    # Layer 2: progressive summary (inspired by SummaryBufferMemory)
    # ------------------------------------------------------------------

    def _prune_oldest_to_summary(self) -> None:
        """Move the oldest pair of messages into the running summary."""
        if len(self.buffer) < 2:
            return
        oldest_pair = self.buffer[:2]
        self.buffer = self.buffer[2:]

        new_content = "\n".join(
            f"[{m.type}] {m.content[:500]}" for m in oldest_pair if m.content
        )
        prompt = (
            "Progressively summarize the conversation, integrating the new lines "
            "into the existing summary. Return ONLY the updated summary, keep it "
            "concise.\n\n"
            f"Existing summary:\n{self.moving_summary or '(empty)'}\n\n"
            f"New lines:\n{new_content}\n\n"
            "Updated summary:"
        )
        response = self.llm.invoke(prompt)
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

        response = self.llm.invoke(
            f"Summarize this entire conversation for continuity. "
            f"Be concise but preserve key decisions and context:\n{full_context}"
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
