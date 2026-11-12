"""可选 Web 依赖的延迟加载与本地启动。"""
import logging
import threading
import webbrowser

from .market import cache_directory


def run_ui(args):
    logging.getLogger("stock_statement").setLevel(logging.DEBUG if args.debug else logging.WARNING)
    try:
        import uvicorn

        from .web import create_app
    except ImportError:
        raise SystemExit('UI 依赖未安装，请运行：uv run --extra ui stock-statement ui') from None

    try:
        app = create_app(ui_directory=args.ui_dir, allowed_hosts={args.host, "localhost", "127.0.0.1"})
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            if self.started:
                port = self.servers[0].sockets[0].getsockname()[1]
                url = f"http://{args.host}:{port}"
                print(f"访问地址：{url}\n公共行情缓存：{cache_directory()}", flush=True)
                if not args.no_browser:
                    def open_browser():
                        try:
                            webbrowser.open(url)
                        except Exception:  # noqa: BLE001 — 浏览器启动失败不能退出服务。
                            print("浏览器未能自动打开，请手动访问上方地址。", flush=True)
                    threading.Thread(target=open_browser, daemon=True).start()
    try:
        Server(uvicorn.Config(app,
                              host=args.host, port=args.port, access_log=False, log_level="warning")).run()
    except KeyboardInterrupt:
        pass
