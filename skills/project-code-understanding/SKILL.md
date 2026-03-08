---
name: project-code-understanding
description: 系统化理解陌生项目代码库：分析目录结构、入口点、核心模块与依赖关系。
---

# 项目代码理解

使用 `read_file`、`bash`、`todo_write`/`todo_list`、`subagent(worker_type="explore")` 系统化探索陌生代码库。读完入口点后必须用任务看板规划步骤，并积极派子智能体完成各子任务。

## 探索工作流

按以下顺序执行，避免遗漏关键信息：

1. **README / 文档** — 了解项目目标、使用方式、技术栈
   - 优先读取：README.md、README、docs/
   - 辅助：pyproject.toml、package.json、Cargo.toml 等

2. **入口点** — 定位程序启动位置
   - Python: main.py、__main__.py、setup.py 的 entry_points
   - Node: package.json 的 main、bin
   - Rust/Go: main.rs、main.go、cmd/

3. **【必须】任务看板规划** — 读完入口点后、开始探索模块之前，**必须**调用 `todo_write` 规划后续步骤
   - 根据入口文件的 import/require 链，拆解为多个子任务（如：探索 agent 模块、探索 tools 模块、分析依赖关系）
   - 使用 `blocked_by` 表达依赖关系
   - 调用 `todo_list` 确认规划后再执行

4. **【必须】子智能体执行** — 积极使用 `subagent(worker_type="explore")` 完成各子步骤
   - 每个子任务（如「探索 agent 模块」「分析 core 模块」）优先派发给 explore Worker
   - 在 subagent prompt 中写明：目标模块、已掌握的入口信息、期望输出
   - 主 Agent 汇总各 Worker 结果，避免在主上下文中堆积大量代码

5. **依赖关系** — 综合各子智能体结果，整理模块边界与外部依赖

## 工具使用指引

| 场景 | 工具 | 用法 |
|------|------|------|
| 读取已知文件 | read_file | README、配置文件、入口文件 |
| 列出目录结构 | bash | `find . -type f -name "*.py" \| head -50` 或 `ls -la` |
| 规划探索步骤 | todo_write | 读完入口点后**必须**调用，拆解子任务 |
| 查看任务状态 | todo_list | 规划后确认，执行中追踪进度 |
| 探索各模块 | subagent | worker_type="explore"，**积极使用**完成子步骤 |
| 搜索符号/模式 | bash | `grep -r "def main" . --include="*.py"`（小范围时） |

**关键**：探索核心模块时，必须先用 todo_write 规划，再派 subagent 逐项完成，避免在主上下文中堆积大量代码。

## 输出格式

汇报时采用结构化格式：

```markdown
# 项目概述
[一句话说明项目用途]

## 目录结构
[关键目录及职责]

## 入口点
[主入口文件及启动方式]

## 核心模块
[关键模块及其职责]

## 依赖关系
[内部模块依赖、外部依赖]
```

## 示例

**用户**：帮我理解这个项目的代码结构

**探索步骤**：
1. read_file("README.md")、read_file("pyproject.toml")
2. read_file("main.py") 或入口文件，识别 import 链
3. **todo_write** 规划：如 `[探索 agent 模块, 探索 tools 模块, 探索 managers 模块, 汇总依赖关系]`，设置 blocked_by
4. **todo_list** 确认规划
5. **subagent(worker_type="explore")** 逐项完成：如「探索 agent/ 目录，说明其职责与关键文件」
6. 汇总各 Worker 结果，按输出模板整理

**输出**：按上述模板填写，突出项目用途与关键模块。
