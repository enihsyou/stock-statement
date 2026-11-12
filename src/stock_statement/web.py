"""本地同源 HTTP API：受限 JSON 上传，无 multipart 或磁盘暂存。"""
import base64
import binascii
import secrets
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .dates import china_today
from .errors import StatementError
from .market import MarketCache
from .quotes import QuoteCache
from .strategies import FeeSettings
from .ui_service import (
    Analysis,
    create_analysis,
    json_value,
    security_market,
    stock_detail,
)
from .uidist import find_ui_directory
from .web_models import DetailResponse, MarketResponse, QuoteResponse

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 24 * 1024 * 1024
MAX_BODY_BYTES = 34 * 1024 * 1024
MAX_SESSIONS = 8


class UploadFileData(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    account: str = Field(default="默认账户", min_length=1, max_length=80)
    data: str


class UploadRequest(BaseModel):
    files: list[UploadFileData] = Field(min_length=1, max_length=20)


class SecurityData(BaseModel):
    id: str
    code: str
    name: str
    market: str


class SessionResponse(BaseModel):
    id: str
    accounts: list[str]
    securities: list[SecurityData]


class ColumnData(BaseModel):
    key: str
    title: str
    numeric: bool
    change: bool


class OverviewData(BaseModel):
    key: str
    title: str
    amount: str
    note: str
    change: bool
    tone: str
    parent: str | None


class ReportResponse(BaseModel):
    metadata: dict
    columns: list[ColumnData]
    rows: list[dict[str, str | None]]
    totals: dict[str, str | None]
    overview: list[OverviewData]
    unknown: list[dict]
    incomplete: bool


class DetailRequest(BaseModel):
    account: str | None = None
    price: Decimal | None = Field(default=None, gt=0)
    candidate: Decimal | None = Field(default=None, gt=0)
    lots: int = Field(default=1, ge=1, le=10000)
    buy_commission: Decimal = Field(default=Decimal("0.0001"), ge=0, le=Decimal("0.01"))
    sell_commission: Decimal = Field(default=Decimal("0.0001"), ge=0, le=Decimal("0.01"))
    buy_minimum: bool = True
    sell_minimum: bool = True
    stamp_tax: Decimal = Field(default=Decimal("0.0005"), ge=0, le=Decimal("0.01"))
    transfer: Decimal = Field(default=Decimal("0.00001"), ge=0, le=Decimal("0.01"))


class Sessions:
    def __init__(self):
        self.items: dict[str, Analysis] = {}

    def get(self, token):
        if token not in self.items:
            raise HTTPException(404, "会话不存在，请重新上传")
        return self.items[token]


class LocalBoundary:
    """在 JSON 解析前限制流式请求体；同时验证 Host 和 Origin。"""
    def __init__(self, app, allowed_hosts):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.active = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope["headers"])
        host = headers.get(b"host", b"").decode()
        hostname = urlsplit("//" + host).hostname
        origin = headers.get(b"origin", b"").decode()
        if hostname not in self.allowed_hosts or (origin and origin != f"http://{host}") or headers.get(b"sec-fetch-site") == b"cross-site":
            return await JSONResponse({"detail": "仅允许本地同源访问"}, 403)(scope, receive, send)
        if self.active >= 2 and scope["method"] == "POST":
            return await JSONResponse({"detail": "并发请求过多，请稍后重试"}, 429)(scope, receive, send)
        body = bytearray()
        if scope["method"] == "POST":
            self.active += 1
            try:
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > MAX_BODY_BYTES:
                        return await JSONResponse({"detail": "上传总量过大"}, 413)(scope, receive, send)
                    if not message.get("more_body"):
                        break
                sent = False
                async def memory_receive():
                    nonlocal sent
                    if not sent:
                        sent = True
                        return {"type": "http.request", "body": bytes(body), "more_body": False}
                    return await receive()
                await self.app(scope, memory_receive, send)
            finally:
                self.active -= 1
                body.clear()
        else:
            await self.app(scope, receive, send)


def create_app(*, market_cache: MarketCache | None = None, allowed_hosts=None,
               ui_directory: Path | None = None) -> FastAPI:
    from .config import load_environment

    load_environment()
    sessions = Sessions()
    market = market_cache or MarketCache()
    quotes = QuoteCache()

    static = find_ui_directory(ui_directory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        sessions.items.clear()
        market.pool.shutdown(wait=False, cancel_futures=True)
        quotes.pool.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Stock Statement Local API", lifespan=lifespan)
    app.state.sessions = sessions
    app.add_middleware(LocalBoundary, allowed_hosts=allowed_hosts or {"127.0.0.1", "localhost", "testserver"})

    @app.middleware("http")
    async def privacy(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "请求字段不合法，请检查文件、数量或费用设置"}, 422)

    @app.exception_handler(StatementError)
    async def statement_error(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    @app.post("/api/sessions", response_model=SessionResponse)
    async def upload(payload: UploadRequest):
        if len(sessions.items) >= MAX_SESSIONS:
            raise HTTPException(429, "内存会话已满，请先清空旧会话")
        files = []
        total = 0
        for file in payload.files:
            try:
                raw = base64.b64decode(file.data, validate=True)
            except (ValueError, binascii.Error):
                raise HTTPException(422, "文件编码无效") from None
            total += len(raw)
            if len(raw) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise HTTPException(413, "单文件上限 8 MiB，总文件上限 24 MiB")
            name = file.name.replace("\\", "/").split("/")[-1]
            files.append((name, file.account, raw))
        analysis = await run_in_threadpool(create_analysis, files)
        token = secrets.token_urlsafe(32)
        sessions.items[token] = analysis
        return SessionResponse(id=token, accounts=list(analysis.accounts), securities=[SecurityData(**item) for item in analysis.securities])

    @app.get("/api/sessions/{token}/report", response_model=ReportResponse)
    async def report(token: str):
        return sessions.get(token).report

    @app.post("/api/sessions/{token}/stocks/{security}", response_model=DetailResponse)
    async def detail(token: str, security: str, settings: DetailRequest):
        analysis = sessions.get(token)
        if security not in {s["id"] for s in analysis.securities}:
            raise HTTPException(404, "证券不存在或不支持股票行情")
        if settings.account is not None and settings.account not in analysis.accounts:
            raise HTTPException(404, "账户不存在")
        fees = FeeSettings(buy_commission=settings.buy_commission, sell_commission=settings.sell_commission,
                           buy_minimum=Decimal(5 if settings.buy_minimum else 0),
                           sell_minimum=Decimal(5 if settings.sell_minimum else 0),
                           stamp_tax=settings.stamp_tax, transfer=settings.transfer)
        return await run_in_threadpool(stock_detail, analysis, security, settings.account, settings.price,
                                      settings.candidate, settings.lots, fees)

    @app.get("/api/market/{market_id}/{code}", response_model=MarketResponse)
    async def candles(market_id: str, code: str, start: str = "20200101", end: str | None = None):
        if market_id not in {"SH", "SZ", "BJ"} or security_market(code) != market_id:
            raise HTTPException(422, "证券标识无效")
        end = end or china_today().strftime("%Y%m%d")
        try:
            date.fromisoformat(start)
            date.fromisoformat(end)
            if len(start) != 8 or len(end) != 8 or start > end or start < "19900101":
                raise ValueError()
        except ValueError:
            raise HTTPException(422, "日线日期范围无效") from None
        return json_value(await run_in_threadpool(market.get, market_id, code, start, end))

    @app.get("/api/quotes/{market_id}/{code}", response_model=QuoteResponse)
    async def quote(market_id: str, code: str):
        if market_id not in {"SH", "SZ", "BJ"} or security_market(code) != market_id:
            raise HTTPException(422, "证券标识无效")
        return json_value(await run_in_threadpool(quotes.get, market_id, code))

    @app.delete("/api/sessions/{token}", status_code=204)
    async def clear(token: str):
        sessions.items.pop(token, None)

    @app.get("/{path:path}", include_in_schema=False)
    async def page(path: str):
        if static is None:
            if path:
                raise HTTPException(404, "页面资源不存在")
            return HTMLResponse("""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><title>Stock Statement</title>
<h1>本地 API 已启动</h1>
<p>前端资源尚未构建。在项目根目录运行：</p>
<pre>pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend dev</pre>
<p>然后访问 Vite 输出的本地地址（默认 http://127.0.0.1:5173）。API 代理默认连接 8765 端口。</p>
<p>也可以运行 <code>pnpm --dir frontend build</code> 后重启后端，
或通过 <code>stock-statement ui --ui-dir /path/to/dist</code> 指定本地资源。</p>
</html>""")
        target = (static / (path or "index.html")).resolve()
        if not target.is_relative_to(static.resolve()) or not target.is_file():
            raise HTTPException(404, "页面资源不存在")
        return FileResponse(target)

    return app
