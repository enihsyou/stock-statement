"""多来源并行查询与共享网络超时。"""

import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, as_completed
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class SourceCooldowns:
    def __init__(self):
        self.lock = threading.Lock()
        self.until: dict[str, float] = {}

    def available(self, source: str) -> bool:
        with self.lock:
            return time.monotonic() >= self.until.get(source, 0)

    def record_failure(self, source: str, error: Exception) -> None:
        from akshare.exceptions import RateLimitError

        current: BaseException | None = error
        while current is not None:
            response = getattr(current, "response", None)
            if (isinstance(current, RateLimitError)
                    or getattr(current, "status_code", None) == 429
                    or getattr(response, "status_code", None) == 429):
                with self.lock:
                    self.until[source] = time.monotonic() + 300
                logger.warning("%s 被限流，暂停查询 5 分钟", source)
                return
            current = current.__cause__ or current.__context__


source_cooldowns = SourceCooldowns()


def first_success[T](futures: Mapping[str, Future[T]], *,
                     is_valid: Callable[[T], bool] = bool,
                     timeout: float | None = None) -> tuple[str, T] | None:
    sources = {future: source for source, future in futures.items()}
    for future in as_completed(sources, timeout=timeout):
        result = future.result()
        if is_valid(result):
            return sources[future], result
    return None


_timeout_lock = threading.Lock()
_timeout_users = 0
_previous_timeout = None


@contextmanager
def akshare_timeout():
    import akshare as ak

    global _timeout_users, _previous_timeout
    # AKShare 超时是进程全局配置，最后一个查询退出后才能恢复。
    with _timeout_lock:
        if _timeout_users == 0:
            _previous_timeout = ak.get_timeout()
            ak.set_timeout(5)
        _timeout_users += 1
    try:
        yield
    finally:
        with _timeout_lock:
            _timeout_users -= 1
            if _timeout_users == 0:
                ak.set_timeout(_previous_timeout)
