#!/usr/bin/env python3
"""Quick test script for ADK-miniCC chat functionality."""

import sys
import warnings

warnings.filterwarnings("ignore", message=".*HMAC key.*", module="jwt.*")

from core import MiniCC

def main():
    agent = MiniCC(workdir=r"D:\Project_Pro\ADK-miniCC")
    
    questions = [
        "你好",
        "你拥有什么工具？",
        "探索一下这个D:\\Project_Pro\\ADK-miniCC项目，并告诉我这个项目是做什么的",
    ]
    
    for i, q in enumerate(questions, 1):
        print(f"\n{'='*60}")
        print(f"问题 {i}: {q}")
        print("="*60)
        try:
            result = agent.chat_with_progress(q, on_status=lambda s: print(f"  [状态] {s}"))
            print(f"\n回答:\n{result.output}")
        except Exception as e:
            print(f"\n[错误] {e}")
            sys.exit(1)
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)

if __name__ == "__main__":
    main()
