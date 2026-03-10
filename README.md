# my-mini-cc

基于 [Google ADK](https://github.com/google/adk-python) 构建的模块化 AI 编码智能体（mini-claudecode），提供交互式 REPL 界面，支持文件读写、命令执行、任务管理、技能加载和代码索引，并通过 coordinator + sub-agent 架构自动将大任务分解委派给独立的 explore / coder / shell 子智能体执行。

## 安装

需要 Python 3.11+ 和 [uv](https://github.com/astral-sh/uv)。

```bash
uv sync
```

## 启动

在目标项目目录下启动，工作区默认为当前目录：

```bash
cd /path/to/your/project
uv run python main.py
```

启动后可用命令：

| 命令 | 说明 |
|---|---|
| `/cd <path>` | 切换工作区目录 |
| `/reset` | 清空会话历史与任务板 |
| `/compact` | 手动压缩上下文 |
| `/tasks` | 查看当前任务板 |
| `/skills` | 查看已加载技能 |
| `/quit` | 退出 |

## 配置

在项目根目录 `cp .env.example .env` 文件，按需填写以下配置项：

参考
```env
# LLM 配置（必填）
LLM_PROVIDER=zhipu          # openai | anthropic | zhipu 等 litellm 支持的提供者
MODEL_ID=glm-4.7            # 模型标识，需与提供者匹配
API_BASE_URL=https://open.bigmodel.cn/api/coding/paas/v4                # 可选，自定义 API 端点（代理或本地模型）

# 工作区（可选，默认为启动时的当前目录）
WORKDIR=D:\Project\my_project

# 上下文压力阈值（可选）
SOFT_TOKEN_LIMIT=64000       # 触发 HIGH 压力警告的 token 数, 约为上下文窗口的1/3
HARD_TOKEN_LIMIT=128000      # 触发强制压缩的 token 数

# 其他可选项见 config.py
```

所有配置项均可通过环境变量覆盖，优先级：环境变量 > `.env` 文件 > 代码默认值。完整配置项列表见 [`config.py`](config.py)。

