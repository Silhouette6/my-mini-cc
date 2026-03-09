"""Centralized configuration — single source of truth for all settings.

通过 Pydantic Settings 统一管理所有配置项。优先级（高→低）：
  1. 构造函数显式传参（MiniCC(llm_provider="anthropic")）
  2. 环境变量（export LLM_PROVIDER=anthropic）
  3. .env 文件中的值
  4. 下方定义的默认值
"""

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

load_dotenv(override=False)


class Settings(BaseSettings):

    # --- LLM 配置 ---
    llm_provider: str = "openai"        # LLM 提供者：openai | anthropic | zhipu
    model_id: str = "gpt-4o"            # 模型标识，需与提供者匹配
    api_base_url: str | None = None     # 自定义 API 端点（代理/本地模型），None 则用官方默认
    llm_request_timeout: int = 30       # LLM API HTTP 请求超时（秒）

    # --- 重试策略 ---
    llm_max_retries: int = 3            # LLM 调用最大重试次数（429 / Timeout）
    llm_retry_min_wait: float = 2.0     # 首次重试等待最小秒数（指数退避起点）
    llm_retry_max_wait: float = 60.0    # 退避等待上限秒数

    # --- 路径配置 ---
    workdir: Path = Path.cwd()          # 工作区根路径，所有相对路径以此为基准
    tasks_dir: str = ".tasks"           # 任务持久化目录（相对 workdir）
    skills_dir: str = "skills"          # 技能插件目录（相对 workdir）
    transcript_dir: str = ".transcripts"  # 对话存档目录（相对 workdir）

    # --- 记忆压缩阈值 ---
    soft_token_limit: int = 40000       # 渐进式摘要触发阈值（token 数）
    hard_token_limit: int = 80000       # 全量压缩触发阈值（token 数）
    memory_tool_retain: int = 4         # 保留的普通工具消息条数
    memory_subagent_retain: int = 8     # 保留的 subagent 返回条数
    memory_load_skill_retain: int = 10   # 保留的 load_skill 返回条数

    # --- 主 Agent ---
    max_iterations: int = 30            # 主 Agent 单轮最大工具调用迭代次数

    # --- Worker 子智能体迭代上限 ---
    worker_explore_max_iter: int = 15   # explore 类型（只读探索）
    worker_coder_max_iter: int = 30     # coder 类型（读写编码）
    worker_shell_max_iter: int = 10     # shell 类型（命令执行）

    # --- REPL 进度展示 ---
    progress_single_line: bool = False   # True: 单行覆盖刷新（不刷屏）；False: 每状态换行持续输出
    progress_status_bash_max: int = 48      # bash 命令在状态栏显示的最大字符数
    progress_status_read_file_max: int = 40  # read_file 路径在状态栏显示的最大字符数
    progress_status_edit_path_max: int = 35  # edit_file/write_file 路径在状态栏显示的最大字符数
    progress_status_generic_max: int = 40   # 其他工具参数在状态栏显示的最大字符数
    progress_status_task_max: int = 48      # todo_write 等 task 工具在状态栏显示的最大字符数
    progress_status_tool_result_max: int = 120   # 工具返回结果在状态栏显示的最大字符数

    # --- 调试 ---
    debug_log_enabled: bool = False     # 开启时记录每轮上下文/模型/工具到 log/
    debug_log_dir: str = "log"          # 日志目录（相对 workdir）

    # --- 安全策略 ---
    dangerous_commands: list[str] = [   # bash 工具拦截的危险命令关键词
        "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"
    ]
    command_timeout: int = 120          # bash 命令超时时间（秒）

    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def tasks_path(self) -> Path:
        return self.workdir / self.tasks_dir

    @property
    def skills_path(self) -> Path:
        return self.workdir / self.skills_dir

    @property
    def transcript_path(self) -> Path:
        return self.workdir / self.transcript_dir


settings = Settings()
