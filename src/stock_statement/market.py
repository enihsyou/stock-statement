"""公共不复权日线缓存；不接受或存储账户与成交信息。"""
import json
import logging
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .config import config_directory
from .dates import china_today
from .parallel import akshare_timeout, first_success, source_cooldowns

SCHEMA = 1
logger = logging.getLogger(__name__)
_source_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="public-market-source")


def cache_directory() -> Path:
    return config_directory() / "cache" / "market-data"


@dataclass(frozen=True)
class Candle:
    time: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    source: str = "东方财富"


@dataclass
class MarketResult:
    candles: list[Candle]
    source: str
    adjustment: str
    updated_at: float
    stale: bool
    error: str | None
    cache_path: str
    provisional: bool = False


def fetch_akshare(market: str, code: str, start: str, end: str) -> list[Candle]:
    def fetch(source: str) -> list[Candle]:
        if not source_cooldowns.available(source):
            return []
        with akshare_timeout():
            try:
                return fetch_source(source, market, code, start, end)
            except Exception as exc:  # 单个数据源故障不影响其他并行查询。
                source_cooldowns.record_failure(source, exc)
                logger.error(
                    "AKShare %s 不复权日线失败：market=%s code=%s range=%s..%s；%s: %s",
                    source, market, code, start, end, type(exc).__name__, exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                return []

    result = first_success({source: _source_pool.submit(fetch, source)
                            for source in ("东方财富", "新浪", "腾讯")
                            if source_cooldowns.available(source)})
    if result is None:
        raise RuntimeError("东方财富、新浪、腾讯均未能获取不复权日线")
    return result[1]


def fetch_source(source: str, market: str, code: str, start: str, end: str) -> list[Candle]:
    import akshare as ak

    symbol = f"{market.lower()}{code}"
    is_fund = code.startswith(("5", "1"))
    if source == "东方财富":
        fetch = ak.fund_etf_hist_em if is_fund else ak.stock_zh_a_hist
        frame = fetch(symbol=code, period="daily", start_date=start, end_date=end, adjust="")
        frame = frame.rename(columns={"日期": "date", "开盘": "open", "最高": "high",
                                      "最低": "low", "收盘": "close", "成交量": "volume"})
    elif source == "新浪":
        frame = (ak.fund_etf_hist_sina(symbol=symbol) if is_fund else
                 ak.stock_zh_a_daily(symbol=symbol, start_date=start, end_date=end, adjust=""))
    else:
        frame = ak.stock_zh_a_hist_tx(symbol=symbol, start_date=start, end_date=end, adjust="")
    rows = []
    for _, row in frame.iterrows():
        day = str(row["date"])[:10]
        if start <= day.replace("-", "") <= end:
            # 保持缓存的手数口径；AKShare 的新浪、腾讯接口返回股／份。
            volume = Decimal(str(row["volume"])) / (1 if source == "东方财富" else 100)
            rows.append(Candle(day, str(row["open"]), str(row["high"]), str(row["low"]),
                               str(row["close"]), str(volume), source))
    if not rows:
        raise ValueError(f"{source} 在请求区间内未返回日线")
    return rows


def source_label(candles: list[Candle]) -> str:
    sources = sorted({row.source for row in candles})
    return "、".join(sources) if sources else "暂无数据"


class MarketCache:
    def __init__(self, directory: Path | None = None, fetcher=fetch_akshare, timeout: float = 20):
        self.directory = directory or cache_directory()
        self.fetcher = fetcher
        self.timeout = timeout
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="public-market")
        self.pending = None

    def get(self, market: str, code: str, start: str, end: str) -> MarketResult:
        with self.lock:
            return self._get(market, code, start, end)

    def _get(self, market: str, code: str, start: str, end: str) -> MarketResult:
        path = self.directory / f"akshare-{market}-{code}-daily-unadjusted-v{SCHEMA}.json"
        cached = None
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached["schema"] != SCHEMA:
                cached = None
            else:
                [Candle(**row) for row in cached["candles"]]
                for key in ("start", "end", "updated_at"):
                    cached[key]
        except (OSError, ValueError, TypeError, KeyError):
            cached = None
        now = time.time()
        candles = {row["time"]: Candle(**row) for row in cached["candles"]} if cached else {}
        ranges = []
        if not cached:
            ranges.append((start, end))
        else:
            if start < cached["start"]:
                ranges.append((start, cached["start"]))
            if end > cached["end"]:
                ranges.append((cached["end"], end))
            today = china_today().strftime("%Y%m%d")
            downloaded_day = datetime.fromtimestamp(cached["updated_at"], timezone(timedelta(hours=8))).strftime("%Y%m%d")
            # 当前交易日不是最终数据；每 15 分钟刷新最近七日以涵盖休市日。
            if ((end >= today and now - cached["updated_at"] > 900)
                    or (downloaded_day < today and cached["end"] >= downloaded_day)):
                ranges.append((max(start, (date.fromisoformat(end) - timedelta(days=7)).strftime("%Y%m%d")), end))
        error = None
        if ranges:
            left, right = ranges[0]
            try:
                for left, right in ranges:
                    if self.pending is not None and not self.pending.done():
                        raise TimeoutError("上一次行情下载仍在后台运行，暂不启动新的请求")
                    self.pending = self.pool.submit(self.fetcher, market, code, left, right)
                    rows = self.pending.result(timeout=self.timeout)
                    for row in rows:
                        candles[row.time] = row
                self.directory.mkdir(parents=True, exist_ok=True)
                payload = {"schema": SCHEMA, "start": min(start, cached["start"]) if cached else start,
                           "end": max(end, cached["end"]) if cached else end, "updated_at": now,
                           "source": source_label(list(candles.values())),
                           "candles": [asdict(row) for row in sorted(candles.values(), key=lambda c: c.time)]}
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.directory, delete=False) as output:
                    temporary = Path(output.name)
                    json.dump(payload, output)
                temporary.replace(path)
                cached = payload
            except Exception as exc:  # 第三方行情错误不能阻断本地账本。
                logger.error(
                    "AKShare 不复权日线获取失败：market=%s code=%s range=%s..%s timeout=%ss cache=%s；%s: %s",
                    market, code, left, right, self.timeout, path, type(exc).__name__, exc,
                    exc_info=logger.isEnabledFor(logging.DEBUG),
                )
                # 第三方错误可能带 URL/响应正文；只返回固定公共错误。
                error = "不复权日线获取失败或超时；已有缓存可能陈旧。"
        filtered = [row for key, row in sorted(candles.items()) if start <= key.replace("-", "") <= end]
        return MarketResult(filtered, source_label(filtered), "不复权", cached["updated_at"] if cached else 0,
                            error is not None, error, str(path),
                            bool(filtered and filtered[-1].time == china_today().isoformat()))
