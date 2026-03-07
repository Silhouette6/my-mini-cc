# MiniCC

基于 LangChain 的模块化编程智能体，支持多 LLM 提供者、插件式技能系统、三层记忆压缩和 Worker 子智能体调度。

## 架构概览

```
core.py (MiniCC Facade)
 ├── agent/executor.py      主 Agent（LangChain create_agent）
 ├── agent/subagent.py      Worker 调度（explore / coder / shell）
 ├── tools/                 9 个工具（bash, read/write/edit, todo, skill, subagent）
 ├── managers/task.py       统一任务管理（持久化 + 依赖图 + 自动解阻塞）
 ├── managers/skill.py      插件式技能（manifest.json + 渐进式披露）
 ├── memory/summary.py      三层压缩记忆 + 上下文压力感知
 ├── config.py              Pydantic Settings 统一配置
 └── llm.py                 LLM 工厂（OpenAI / Anthropic / 智谱 / 自定义）
```

## Quickstart

### 1. 安装依赖

**方式 A：使用 uv（推荐）**

```bash
cd my-mini-cc
uv sync
```

`uv sync` 会自动创建 `.venv` 虚拟环境并安装 `pyproject.toml` 中的依赖。未安装 uv 可执行：`pip install uv` 或访问 [github.com/astral-sh/uv](https://github.com/astral-sh/uv)。

**方式 B：使用 pip**

```bash
cd my-mini-cc
pip install -r requirements.txt
```

### 2. 配置环境变量

在 `my-mini-cc/` 目录下创建 `.env` 文件：

```env
# --- 选择 LLM 提供者（三选一）---

# OpenAI / OpenAI 兼容 API
LLM_PROVIDER=openai
MODEL_ID=gpt-4o
OPENAI_API_KEY=sk-your-key-here
# API_BASE_URL=https://your-proxy.com/v1    # 可选，自定义端点

# Anthropic
# LLM_PROVIDER=anthropic
# MODEL_ID=claude-sonnet-4-20250514
# ANTHROPIC_API_KEY=sk-ant-your-key-here

# 智谱 AI
# LLM_PROVIDER=zhipu
# MODEL_ID=glm-4
# ZHIPUAI_API_KEY=your-key-here
```

### 3. 启动 REPL

```bash
cd my-mini-cc
uv run python main.py
```

若使用 pip 安装，则直接运行 `python main.py`。

```
mini-cc ready.  Commands: /compact  /tasks  /skills  /quit

mini-cc >> 帮我分析当前目录结构
```

### REPL 命令

| 命令 | 功能 |
|------|------|
| `/tasks` | 查看当前任务列表 |
| `/skills` | 查看可用技能 |
| `/compact` | 手动压缩上下文记忆 |
| `/quit` | 退出 |

## Python API

MiniCC 也可以作为 Python 库使用：

```python
from core import MiniCC

# 创建 agent 实例
agent = MiniCC(
    workdir="/path/to/project",
    llm_provider="openai",
    model_id="gpt-4o",
)

# 对话
result = agent.chat("帮我分析项目结构")
print(result.output)
print(result.token_usage)  # 上下文压力指标

# 流式输出
for chunk in agent.stream("重构这个函数"):
    print(chunk, end="", flush=True)

# 管理
agent.tasks.list_all()         # 查看任务
agent.skills.summaries()       # 查看技能
agent.compact()                # 手动压缩
agent.reset()                  # 清空记忆

# 无状态单次调用（适合 MCP / 子 agent 场景）
output = MiniCC.quick_run("列出所有 TODO", workdir="/project")
```

## 配置项一览

所有配置通过 `config.py` 的 `Settings` 类管理，可在 `.env` 中覆盖：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_PROVIDER` | `openai` | LLM 提供者：`openai` / `anthropic` / `zhipu` |
| `MODEL_ID` | `gpt-4o` | 模型标识 |
| `API_BASE_URL` | `None` | 自定义 API 端点（代理/本地模型） |
| `WORKDIR` | 当前目录 | 工作区根路径 |
| `TASKS_DIR` | `.tasks` | 任务持久化目录（相对 WORKDIR） |
| `SKILLS_DIR` | `skills` | 技能插件目录（相对 WORKDIR） |
| `TRANSCRIPT_DIR` | `.transcripts` | 对话存档目录（相对 WORKDIR） |
| `SOFT_TOKEN_LIMIT` | `40000` | 渐进式摘要触发阈值 |
| `HARD_TOKEN_LIMIT` | `80000` | 全量压缩触发阈值 |
| `MAX_ITERATIONS` | `30` | 主 Agent 最大迭代次数 |
| `WORKER_EXPLORE_MAX_ITER` | `15` | explore Worker 最大迭代 |
| `WORKER_CODER_MAX_ITER` | `30` | coder Worker 最大迭代 |
| `WORKER_SHELL_MAX_ITER` | `10` | shell Worker 最大迭代 |
| `PROGRESS_SINGLE_LINE` | `true` | REPL 进度：true 单行覆盖，false 每状态换行输出 |
| `COMMAND_TIMEOUT` | `120` | shell 命令超时（秒） |

## 技能插件开发

在 `skills/` 下创建子目录，包含 `manifest.json` 和 `SKILL.md`：

```
skills/
└── my-skill/
    ├── manifest.json       # 元数据 + 工具声明
    ├── SKILL.md            # 完整说明（加载后可见）
    └── tools/
        └── my_tool.py      # 工具脚本
```

`manifest.json` 示例：

```json
{
  "name": "my-skill",
  "description": "一句话描述，Agent 据此决定是否加载",
  "tools": [
    {
      "name": "my_tool",
      "description": "工具功能说明",
      "script": "tools/my_tool.py",
      "args": {
        "input_file": {
          "type": "string",
          "description": "输入文件路径",
          "required": true
        }
      }
    }
  ]
}
```

Agent 的使用流程：
1. 系统启动时，Agent 在 prompt 中看到技能摘要（仅 name + description）
2. Agent 决定加载 → `load_skill("my-skill")` → 看到完整 SKILL.md + 工具列表
3. Agent 调用工具 → `run_skill_tool("my-skill", "my_tool", '{"input_file": "data.csv"}')`

## 三层记忆压缩

| 层级 | 触发条件 | LLM 开销 | 说明 |
|------|---------|---------|------|
| Layer 1: microcompact | 每轮 | 零 | 清除旧 tool_result 内容，保留最近 3 个 |
| Layer 2: 渐进式摘要 | token > soft_limit | 低 | 逐步将最老消息合入 moving_summary |
| Layer 3: 硬压缩 | token > hard_limit 或 `/compact` | 高 | 全量摘要 + transcript 存盘 |

Agent 能感知当前上下文压力（LOW / MEDIUM / HIGH），压力高时会自动选择 subagent 执行探索任务以减轻主上下文负担。

## Worker 子智能体

主 Agent 可派生三种临时 Worker：

| 类型 | 工具 | 适用场景 |
|------|------|---------|
| `explore` | bash, read_file | 代码搜索、结构分析 |
| `coder` | bash, read/write/edit | 隔离编码任务 |
| `shell` | bash | 命令执行与分析 |

Worker 跑完即销毁，无记忆，无递归（不能再派生子 Worker）。

## 扩展路径

MiniCC 的 Facade API 设计为可复用：

- **MCP Server**：包装 `agent.chat()` 为 MCP tool
- **子 agent**：其他 agent 系统通过 `MiniCC.quick_run()` 调用
- **Web API**：FastAPI 包装
- **多 agent 协作**：未来可用 LangGraph StateGraph 扩展
