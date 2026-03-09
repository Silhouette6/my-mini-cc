#!/usr/bin/env python3
"""Thin REPL — the CLI entry point for MiniCC."""

import sys
import warnings

import config
from core import MiniCC

# 智谱 API Key 格式触发 PyJWT 的 HMAC 长度警告，可安全忽略
warnings.filterwarnings("ignore", message=".*HMAC key.*", module="jwt.*")


def main() -> None:
    agent = MiniCC()
    print("mini-cc ready.  Commands: /compact  /reset  /tasks  /skills  /quit")
    print()

    while True:
        try:
            query = input("\033[36mmini-cc >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break

        stripped = query.strip().lower()

        if stripped in ("", "q", "exit", "quit", "/quit"):
            if stripped == "":
                continue
            print("Bye.")
            break

        if stripped == "/compact":
            agent.compact()
            print("[compacted]")
            continue

        if stripped == "/reset":
            agent.reset()
            print("[memory and tasks cleared]")
            continue

        if stripped == "/tasks":
            print(agent.tasks.list_all())
            continue

        if stripped == "/skills":
            print(agent.skills.summaries())
            continue

        def on_status(s: str) -> None:
            color = "\033[90m" if s.startswith("Result:") else "\033[38;5;94m"
            if config.settings.progress_single_line:
                sys.stdout.write(f"\r{color}{s}\033[0m   ")
                sys.stdout.flush()
            else:
                print(f"{color}{s}\033[0m")

        result = agent.chat_with_progress(query, on_status=on_status)
        print()
        print(result.output)
        print(f"\n\033[90m{result.token_usage}\033[0m")
        print()


if __name__ == "__main__":
    main()
