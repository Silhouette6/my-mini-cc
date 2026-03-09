"""Worker dispatch system — hub-and-spoke, no recursion.

Three predefined worker types (explore / coder / shell) configured via
WORKER_TYPES. Each worker is an ephemeral agent created per call,
with no memory and no access to the subagent tool (prevents recursion).
"""

from __future__ import annotations

from langchain.agents import create_agent
from langchain_core.tools import tool

import config
from llm import create_llm
from tools.base import BASE_TOOLS, READ_ONLY_TOOLS

WORKER_TYPES: dict[str, dict] = {
    "explore": {
        "description": "Read-only exploration: search code, understand structure, answer questions",
        "tools_key": "read_only",
        "max_iterations_key": "worker_explore_max_iter",
        "system_hint": (
            "You are a code explorer. Read-only — never write files. "
            "Search quickly and summarize your findings concisely."
        ),
    },
    "coder": {
        "description": "Code modification: read & write files for isolated coding tasks",
        "tools_key": "full",
        "max_iterations_key": "worker_coder_max_iter",
        "system_hint": (
            "You are a coder. Follow the instructions precisely to complete "
            "the coding task. Summarize all changes you made when done."
        ),
    },
    "shell": {
        "description": "Command execution: run shell commands and analyze output",
        "tools_key": "shell_only",
        "max_iterations_key": "worker_shell_max_iter",
        "system_hint": (
            "You are a command execution specialist. Run commands and "
            "analyze the results. Provide your conclusions when done."
        ),
    },
}

_TOOL_SETS = {
    "read_only": lambda: list(READ_ONLY_TOOLS),
    "full": lambda: list(BASE_TOOLS),
    "shell_only": lambda: [BASE_TOOLS[0]],  # bash only
}


def _build_worker(worker_type: str, prompt_text: str):
    """Create an ephemeral agent for a worker — no memory, no subagent."""
    spec = WORKER_TYPES.get(worker_type)
    if spec is None:
        raise ValueError(
            f"Unknown worker_type '{worker_type}'. "
            f"Choose from: {', '.join(WORKER_TYPES)}"
        )

    worker_tools = _TOOL_SETS[spec["tools_key"]]()
    llm = create_llm()

    system_prompt = f"{spec['system_hint']}\nWorkspace: {config.settings.workdir}"

    return create_agent(
        model=llm,
        tools=worker_tools,
        system_prompt=system_prompt,
        name=f"worker-{worker_type}",
    )


@tool
def subagent(prompt: str, worker_type: str = "explore") -> str:
    """Spawn a temporary Worker agent to execute an isolated task and return a summary.

    The Worker has NO memory and is destroyed after completion.
    The Worker CANNOT see your conversation history — the prompt must be self-contained.

    worker_type choices:
      - "explore": read-only exploration (bash + read_file). Use for code search
                   and understanding.
      - "coder":   can read & write files. Use for isolated coding tasks.
      - "shell":   command execution only (bash). Use for running and analyzing
                   command output.

    WARNING: For simple tasks (read one file, run one command, make one edit),
    do it yourself instead of spawning a subagent.
    """
    try:
        worker = _build_worker(worker_type, prompt)
        result = worker.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
            config={"recursion_limit": 40},
        )
        messages = result.get("messages", [])
        if messages:
            last = messages[-1]
            content = last.content if hasattr(last, "content") else str(last)
            return content or "(no output)"
        return "(no output)"
    except Exception as e:
        return f"Error in subagent: {e}"
