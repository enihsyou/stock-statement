# 开发与构建

前端使用 React、TypeScript、Vite、官方 shadcn CLI 生成的 Radix 组件及 Tailwind 样式，图表使用 Lightweight Charts。Node 只用于开发与构建。

```powershell
uv sync --extra ui
uv run --extra ui stock-statement ui --no-browser
# 另一个终端
cd frontend
pnpm install --frozen-lockfile
pnpm dev
# 开发代理将 /api 发往本地 8765
# 在 Vite 输出的地址访问前端，默认 http://127.0.0.1:5173
pnpm typecheck
pnpm test
pnpm build
cd ..
uv build
```

前端与 Python 包独立构建。`pnpm build` 输出到 `frontend/dist/`，生成文件不纳入 Git；`uv build` 只构建 Python 包，不调用 pnpm，也不包含前端资源。

本地开发时，后端无需前端产物即可启动，根页面会显示开发指引。运行 `pnpm dev` 后访问 Vite 输出的地址，使用热更新；现有 `/api` 代理默认连接 `127.0.0.1:8765`，因此后端使用默认端口。

需要由 Python 服务提供页面时，先手动运行 `pnpm --dir frontend build`，再启动后端。源码环境默认读取 `frontend/dist/`，修改前端后需重新构建。也可以通过 `uv run --extra ui stock-statement ui --ui-dir ./frontend/dist` 显式指定包含 `index.html` 的目录；安装 wheel 后同样可以使用 `stock-statement ui --ui-dir /path/to/dist`，运行静态页面无需 Node。显式目录不存在或缺少入口文件时会报错。

安装 Python 包后，首次启动 UI 会从 GitHub Release 下载与包版本完全一致的 `stock-statement-ui-<版本>.tar.gz` 和 `.sha256`，通过 `https://gh-proxy.org/` 代理访问。SHA-256 校验通过后，在临时目录安全解压，再移入版本缓存；资源只允许普通文件和目录。缓存位置为 Windows `%APPDATA%/stock-statement/cache/ui/<版本>/`，其他环境为 `~/.config/stock-statement/cache/ui/<版本>/`。缓存命中后无需联网，不会使用其他版本的资源。需要重新下载时删除对应版本目录。

下载或校验失败会停止启动并报告原因，可修复网络后重试，或手动下载、解压对应前端包，再通过 `--ui-dir` 启动。显式指定目录优先于源码产物和缓存；源码环境缺少构建产物时保留开发指引，不自动下载 Release。

## 发布

采用 GitHub Release 分别分发 Python 包与前端资源，不需要将前端打入 wheel，也无需用户安装 Node。`.github/workflows/release.yml` 在推送 `v*` 标签时执行：

1. 检查标签与 `pyproject.toml` 的版本一致，标签格式为 `v<版本>`。
2. 使用锁文件安装前端依赖并构建，使用 `uv build` 生成 wheel 和源码包。
3. 将 `frontend/dist/` 的内容归档为 `stock-statement-ui-<版本>.tar.gz`，入口 `index.html` 位于归档根目录；生成同名 `.sha256` 文件，采用 `sha256sum` 格式。
4. 将全部文件上传到草稿 Release，上传完成后公开发布。当前不向 PyPI 上传。

发布前更新 `pyproject.toml`、`frontend/package.json` 的版本并用 `uv lock`、`pnpm --dir frontend install --lockfile-only` 更新锁文件，再提交并推送对应标签。仅修改文件或提交代码不会发布；推送标签才会触发工作流。

对应 Release 发布后，可指定目标版本并直接从 wheel 安装：

```powershell
$releaseVersion = Read-Host "输入目标 Release 版本（不含 v）"
uv tool install --python 3.14 "stock-statement[ui] @ https://gh-proxy.org/https://github.com/enihsyou/stock-statement/releases/download/v${releaseVersion}/stock_statement-${releaseVersion}-py3-none-any.whl"
stock-statement ui
```

离线分发时，提前下载 wheel、Python 依赖及同版本前端包；使用 `--ui-dir` 指向已解压的前端目录。

OpenAPI 变更后重新生成类型：

```powershell
uv run --extra ui python -c "import json; from pathlib import Path; from stock_statement.web import create_app; Path('frontend/openapi.json').write_text(json.dumps(create_app().openapi()), encoding='utf-8')"
pnpm --dir frontend exec openapi-typescript openapi.json -o src/api-schema.ts
```

核心新增模块：`strategies.py` 为纯计算；`ui_service.py` 为共享应用服务；`web_models.py` 为响应契约；`web.py` 为内存 API；`market.py` 为公共行情缓存；`ui.py` 为可选依赖启动入口。

## 行情配置

雪球现价查询使用环境变量 `xq_a_token`。服务启动时也会加载配置目录中的 `.env`，已有环境变量优先：Windows 为 `%APPDATA%/stock-statement/.env`，其他环境为 `~/.config/stock-statement/.env`。文件内容示例：

```dotenv
xq_a_token=你的雪球令牌
```

修改 `.env` 后重启服务生效。

## 当前限制与验证说明

数据核验优先直接调用 API，无需打开浏览器。`tests/test_web.py` 使用 FastAPI `TestClient` 覆盖上传、报告和证券详情，包括零持仓的 A 策略参考、现价含费用可执行数量、跨券商持仓隔离与负净投入收益率；`tests/test_quotes.py` 覆盖行情昨收、涨跌和缓存响应。匿名用例不依赖本地私有交割单：

```powershell
uv run --extra ui pytest tests/test_web.py tests/test_quotes.py tests/test_strategies.py -q
```

`tests/test_local_data_api.py` 还可直接通过上传与详情 API 重现本地东方财富 518600 和招商净投入案例；缺少对应 `data/` 文件时自动跳过，不将真实流水加入测试仓库：

```powershell
uv run --extra ui pytest tests/test_local_data_api.py -q
```

- 原 CLI 遇到缺少期初数量、卖出超过历史持仓仍会停止核算；UI 延续此错误，不虚构零成本。送转、拆并股、转托等非买卖事件会令新策略不完整并隐藏失真期待线，原账本仍按既有规则处理。
- 实际可用持仓、资金、交易日历、涨跌停限制、证券特定最小委托量没有券商接口核验。计划是交割单推演，不是下单指令。
- 用户指定的 `data/Table_6188.txt` 已完成真实文件验收：独立生成 CLI 报告投影，逐列核对 API 首页金额、合计和概览一致；全部支持证券的双策略详情正常返回。上传、报告、详情和删除全程未写盘，删除后会话释放。`data/Table_2368.txt` 属于已知错误输入，只用于核对 CLI/API 拒绝行为。真实文件和报告不加入仓库。
- 当前环境真实 AKShare 查询曾返回东方财富 `Push2RateLimitError`（IP 临时限流）。缓存与失败路径有测试，不能将该次真实行情查询声称为成功。

类型检查覆盖 `src/` 与 `tests/`，使用项目 `.venv`。手动浏览器验收依赖放在独立 `browser` 依赖组，可用 `uv sync --extra ui --group browser` 安装，再运行 `uvx pyright`。`out/` 是本地临时产物，不属于项目类型检查范围。前端构建将 Lightweight Charts 拆为独立 `charts` 包，保持静态引用和预加载，不调整资源大小告警阈值。
