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

# 将 .env 中所有键值加载到 os.environ，
# 使 LangChain 各集成包能自动读取 API Key（OPENAI_API_KEY 等）
load_dotenv(override=False)


class Settings(BaseSettings):

    # --- LLM 配置 ---
    llm_provider: str = "openai"        # LLM 提供者：openai | anthropic | zhipu
    model_id: str = "gpt-4o"            # 模型标识，需与提供者匹配
    api_base_url: str | None = None     # 自定义 API 端点（代理/本地模型），None 则用官方默认

    # --- 路径配置 ---
    workdir: Path = Path.cwd()          # 工作区根路径，所有相对路径以此为基准
    tasks_dir: str = ".tasks"           # 任务持久化目录（相对 workdir）
    skills_dir: str = "skills"          # 技能插件目录（相对 workdir）
    transcript_dir: str = ".transcripts"  # 对话存档目录（相对 workdir）

    # --- 记忆压缩阈值 ---
    soft_token_limit: int = 40000       # Layer 2 渐进式摘要触发阈值（token 数）
    hard_token_limit: int = 80000       # Layer 3 全量压缩触发阈值（token 数）

    # --- 主 Agent ---
    max_iterations: int = 30            # 主 Agent 单轮最大工具调用迭代次数

    # --- Worker 子智能体迭代上限 ---
    worker_explore_max_iter: int = 15   # explore 类型（只读探索）
    worker_coder_max_iter: int = 30     # coder 类型（读写编码）
    worker_shell_max_iter: int = 10     # shell 类型（命令执行）

    # --- REPL 进度展示 ---
    progress_single_line: bool = True   # True: 单行覆盖刷新（不刷屏）；False: 每状态换行持续输出
    progress_status_bash_max: int = 48      # bash 命令在状态栏显示的最大字符数
    progress_status_read_file_max: int = 40  # read_file 路径在状态栏显示的最大字符数
    progress_status_edit_path_max: int = 35  # edit_file/write_file 路径在状态栏显示的最大字符数
    progress_status_generic_max: int = 40   # 其他工具参数在状态栏显示的最大字符数

    # --- 安全策略 ---
    dangerous_commands: list[str] = [   # bash 工具拦截的危险命令关键词
        "rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"
    ]
    command_timeout: int = 120          # bash 命令超时时间（秒）

    # Pydantic Settings：从 .env 读取，忽略未声明的字段
    model_config = {"env_file": ".env", "extra": "ignore"}

    @property
    def tasks_path(self) -> Path:
        """任务持久化绝对路径：workdir / tasks_dir"""
        return self.workdir / self.tasks_dir

    @property
    def skills_path(self) -> Path:
        """技能插件绝对路径：workdir / skills_dir"""
        return self.workdir / self.skills_dir

    @property
    def transcript_path(self) -> Path:
        """对话存档绝对路径：workdir / transcript_dir"""
        return self.workdir / self.transcript_dir


# 全局单例，各模块通过 from config import settings 引用
# MiniCC.__init__ 中如有 override 会替换此实例
settings = Settings()
