"""LLM API 重试工具 — 处理 429 速率限制和 HTTP 超时。

设计说明
--------
不在 LLM 工厂层使用 .with_retry()，因为那会返回 RunnableRetry 对象，
而 LangChain create_agent() 内部需要调用 llm.bind_tools()，该方法只
存在于 BaseChatModel，RunnableRetry 没有该方法。

因此重试在两个层面分别实现：

1. memory/summary.py 中直接调 self.llm.invoke()：
      from utils.retry import with_llm_retry
      response = with_llm_retry(self.llm.invoke, prompt)

2. core.py 中整个 agent.stream() / agent.invoke() 循环：
      from tenacity import Retrying
      from utils.retry import is_retryable, before_sleep_log, make_retry_kwargs
      for attempt in Retrying(**make_retry_kwargs()):
          with attempt:
              messages = list(input_messages)   # 每次 attempt 重置
              for chunk in self._agent.stream(...):
                  ...
"""

from __future__ import annotations

import sys
from typing import Any, Callable, TypeVar

from tenacity import (
    RetryCallState,
    Retrying,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

F = TypeVar("F", bound=Callable[..., Any])


def is_retryable(exc: BaseException) -> bool:
    """返回 True 表示该异常应当触发重试。

    可重试条件：
    - httpx.HTTPStatusError 且状态码为 429（速率限制）
    - httpx.TimeoutException 及其子类（ReadTimeout、ConnectTimeout 等）
    """
    try:
        import httpx
    except ImportError:
        return False

    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    if isinstance(exc, httpx.TimeoutException):
        return True
    return False


def before_sleep_log(retry_state: RetryCallState) -> None:
    """重试前输出警告信息，便于用户感知等待过程。"""
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number
    wait = getattr(retry_state.next_action, "sleep", None)
    wait_str = f"{wait:.1f}s" if wait is not None else "?"

    if exc is not None:
        try:
            import httpx
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                reason = "429 Too Many Requests"
            elif isinstance(exc, httpx.TimeoutException):
                reason = f"HTTP Timeout ({type(exc).__name__})"
            else:
                reason = type(exc).__name__
        except ImportError:
            reason = type(exc).__name__
    else:
        reason = "unknown error"

    print(
        f"\033[33m[retry] {reason} — 第 {attempt} 次重试，等待 {wait_str}...\033[0m",
        file=sys.stderr,
    )


def make_retry_kwargs() -> dict:
    """返回可直接传给 tenacity.Retrying(**...) 的 kwargs 字典。

    示例::

        from tenacity import Retrying
        from utils.retry import make_retry_kwargs

        for attempt in Retrying(**make_retry_kwargs()):
            with attempt:
                result = some_llm_call()
    """
    from config import settings

    return dict(
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(settings.llm_max_retries + 1),
        wait=wait_exponential_jitter(
            initial=settings.llm_retry_min_wait,
            max=settings.llm_retry_max_wait,
        ),
        before_sleep=before_sleep_log,
        reraise=True,
    )


def llm_retry() -> Callable[[F], F]:
    """返回一个 tenacity @retry 装饰器，从当前 settings 读取重试配置。

    示例::

        @llm_retry()
        def call_llm():
            return llm.invoke(prompt)
    """
    from config import settings

    return retry(  # type: ignore[return-value]
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(settings.llm_max_retries + 1),
        wait=wait_exponential_jitter(
            initial=settings.llm_retry_min_wait,
            max=settings.llm_retry_max_wait,
        ),
        before_sleep=before_sleep_log,
        reraise=True,
    )


def with_llm_retry(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """将任意可调用 fn(*args, **kwargs) 用重试策略包裹后执行。

    示例::

        result = with_llm_retry(llm.invoke, prompt)
    """
    return llm_retry()(fn)(*args, **kwargs)
