"""并行获取公开价格，复用全市场快照。"""

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, time as clock_time, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .market import cache_directory
from .parallel import akshare_timeout, first_success, source_cooldowns

logger = logging.getLogger(__name__)
ERROR = "实时价格暂不可用"


@dataclass(frozen=True)
class QuoteResult:
    market: str
    code: str
    price: str | None
    updated_at: float | None
    source: str
    error: str | None
    previous_close: str | None = None
    change: str | None = None
    change_percent: str | None = None


@dataclass(frozen=True)
class PriceSnapshot:
    price: str
    previous_close: str | None

    def result(self, market: str, code: str, updated_at: float, source: str) -> QuoteResult:
        change = Decimal(self.price) - Decimal(self.previous_close) if self.previous_close else None
        percent = change / Decimal(self.previous_close) * 100 if change is not None and self.previous_close else None
        return QuoteResult(
            market, code, self.price, updated_at, source, None,
            self.previous_close, str(change) if change is not None else None,
            str(percent) if percent is not None else None,
        )


def _price(value: object) -> str | None:
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and number > 0 else None
    except InvalidOperation:
        return None


def fetch_prices(source: str, market: str, code: str) -> dict[str, PriceSnapshot]:
    import akshare as ak

    with akshare_timeout():
        if source == "雪球":
            frame = ak.stock_individual_spot_xq(symbol=f"{market}{code}", token=os.environ.get("xq_a_token"))
            values = dict(zip(frame["item"], frame["value"]))
            price = _price(values.get("现价"))
            return {code: PriceSnapshot(price, _price(values.get("昨收")))} if price is not None else {}
        frame = ak.stock_zh_a_spot_em() if source == "东方财富" else ak.stock_zh_a_spot()
        prices = {}
        for symbol, value, previous_close in zip(frame["代码"], frame["最新价"], frame["昨收"]):
            price = _price(value)
            if price is not None:
                prices[str(symbol)[-6:].zfill(6)] = PriceSnapshot(price, _price(previous_close))
        return prices


class QuoteCache:
    def __init__(self, fetcher: Callable[[str, str, str], dict[str, PriceSnapshot]] = fetch_prices,
                 timeout: float = 15):
        self.fetcher = fetcher
        self.timeout = timeout
        self.pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="public-quote")
        self.lock = threading.Lock()
        self.cached: dict[str, tuple[float, float, dict[str, PriceSnapshot]]] = {}
        self.pending: dict[str, Future[tuple[float, dict[str, PriceSnapshot]]]] = {}

    def get(self, market: str, code: str) -> QuoteResult:
        now = datetime.now(timezone(timedelta(hours=8)))
        day = now.date()
        if now.weekday() < 5 and clock_time(9, 15) <= now.time() < clock_time(15):
            closing_day = None
        else:
            if now.time() < clock_time(15):
                day -= timedelta(days=1)
            while day.weekday() >= 5:
                day -= timedelta(days=1)
            closing_day = day
        path = cache_directory() / f"quote-{market}-{code}.json"
        if closing_day is not None:
            try:
                cached = QuoteResult(**json.loads(path.read_text(encoding="utf-8")))
                if cached.updated_at is not None:
                    cached_at = datetime.fromtimestamp(cached.updated_at, now.tzinfo)
                    if cached_at.date() == closing_day and cached_at.time() >= clock_time(15):
                        return cached
            except (OSError, ValueError, TypeError, KeyError):
                pass
        # 两个全市场接口只含 A 股，场内基金直接使用支持基金的雪球接口。
        sources = ("雪球",) if code.startswith(("5", "1")) else ("东方财富", "新浪", "雪球")
        futures = {}
        for source in sources:
            key = f"{source}:{market}:{code}" if source == "雪球" else source
            with self.lock:
                cached = self.cached.get(key)
                if cached and cached[0] > time.monotonic():
                    future = Future()
                    future.set_result((cached[1], cached[2]))
                else:
                    if not source_cooldowns.available(source):
                        continue
                    future = self.pending.get(key)
                    if future is None or future.done():
                        future = self.pool.submit(self._fetch, key, source, market, code)
                        self.pending[key] = future
                futures[source] = future
        try:
            result = first_success(futures, is_valid=lambda value: code in value[1], timeout=self.timeout)
        except TimeoutError:
            result = None
        if result is not None:
            source, (updated_at, prices) = result
            quote = prices[code].result(market, code, updated_at, source)
            fetched_at = datetime.fromtimestamp(updated_at, now.tzinfo)
            if closing_day is not None and fetched_at.date() == closing_day and fetched_at.time() >= clock_time(15):
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(json.dumps(asdict(quote), ensure_ascii=False), encoding="utf-8")
                except OSError:
                    logger.warning("无法写入现价缓存：%s", path)
            return quote
        return QuoteResult(market, code, None, None, "暂无数据", ERROR)

    def _fetch(self, key: str, source: str, market: str, code: str) -> tuple[float, dict[str, PriceSnapshot]]:
        if not source_cooldowns.available(source):
            return time.time(), {}
        try:
            prices = self.fetcher(source, market, code)
        except Exception as exc:  # 单个行情源故障不阻断其他源或本地分析。
            source_cooldowns.record_failure(source, exc)
            logger.warning("AKShare %s 价格获取失败：%s %s；%s: %s",
                           source, market, code, type(exc).__name__, exc,
                           exc_info=logger.isEnabledFor(logging.DEBUG))
            prices = {}
        updated_at = time.time()
        with self.lock:
            self.cached[key] = (time.monotonic() + (30 if prices else 10), updated_at, prices)
        return updated_at, prices
