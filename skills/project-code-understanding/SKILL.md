---
name: project-code-understanding
description: 系统化理解陌生项目代码库：分析目录结构、入口点、核心模块、依赖关系与项目人员。
---

# 项目代码理解

使用 `read_file`、`bash`、`todo_write` 系统化探索陌生代码库。读完入口点后必须用任务看板规划步骤，然后按看板逐项执行。

## 探索流程

1. **README + 入口点** — 读取 README 了解项目目标与技术栈，定位程序启动位置
   - 文档：README.md、docs/、pyproject.toml / package.json / Cargo.toml
   - 入口：main.py、\_\_main\_\_.py（Python）；package.json `main`/`bin`（Node）；main.go / main.rs（Go/Rust）

2. **【必须】todo 看板规划** — 读完入口点后、开始探索模块之前，**必须**调用 `todo_write` 拆解子任务
   - 根据 import/require 链拆解模块子任务，用 `blocked_by` 表达依赖
   - 同时加入「获取项目人员信息」任务（git 贡献者分析）

3. **按看板执行** — 模型根据任务量自主判断是否派 `subagent(worker_type="explore")`
   - 单文件/单符号：直接 `read_file` / `get_symbol_body`
   - 大范围探索/多文件扫描：推荐派 subagent，避免主上下文膨胀

4. **汇总输出** — 按下方模板整理结果

## 工具使用指引

| 场景 | 工具 / 命令 | 说明 |
|------|-------------|------|
| 读已知文件 | `read_file` | README、配置文件、入口文件 |
| 列目录结构 | `bash: find . -type f -name "*.py" \| head -50` | 快速摸清文件布局 |
| 获取符号实现 | `get_symbol_body(file_path, symbol_name)` | 优先于 read_file，只返回目标代码省 token |
| 规划步骤 | `todo_write` | 读完入口点后**必须**调用 |
| 大范围探索 | `subagent(worker_type="explore")` | 推荐用于多模块并行探索 |
| 搜索符号/模式 | `bash: grep -r "def main" . --include="*.py"` | 小范围精确搜索 |

**积极使用 `bash` 执行 git 命令**，从版本历史中挖掘项目演进脉络与人员信息：

| 样例 git 场景 | 命令示例 | 说明 |
|----------|----------|------|
| 提交历史 | `git log --oneline --graph -20` / `git log --author="name" --since="2024-01-01"` | 全局历史；按作者、日期、文件路径过滤 |
| 提交详情 | `git show <hash>` / `git show HEAD~1` | 完整 diff，支持短哈希/HEAD 引用 |
| 文件历史 | `git log --follow -p -- <file>` | 单文件全部修改记录，含 rename 追踪 |
| 分支对比 | `git diff <base>..<head> --stat` / `git diff <base>..<head>` | 变更文件列表 + 详细 diff |
| 逐行归因 | `git blame -L <start>,<end> <file>` | 每行最后由谁在哪次提交中修改 |
| 分支信息 | `git branch -a -vv` | 本地+远程分支、超前/落后状态、最新提交 |
| 工作区状态 | `git status` | 已暂存、已修改、未跟踪、合并冲突 |
| 贡献者汇总 | `git shortlog -sn --no-merges` / `git log --format="%an <%ae>" \| sort -u` | 按提交数排序；去重邮箱列表 |

## 输出格式

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

## 项目人员
[主要贡献者列表（提交数）、活跃时间段、核心模块负责人（来自 blame/log 分析）]
```
