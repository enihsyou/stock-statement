"""匿名端到端验收；独立服务、行情缓存及截图均限于临时目录。"""

import asyncio
import os
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import uvicorn
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from stock_statement.market import Candle, MarketCache
from stock_statement.web import create_app
from tests.factory import write_statement


def fetch(market, code, start, end):
    return [
        Candle(
            (date(2026, 10, 20) + timedelta(days=i)).isoformat(),
            "12",
            "16",
            "8",
            "13",
            "1000",
        )
        for i in range(24)
    ]


columns = [
    "成交日期",
    "交收日期",
    "业务名称",
    "证券代码",
    "证券名称",
    "成交数量",
    "成交金额",
    "发生金额",
    "币种",
    "流水号",
    "佣金",
    "印花税",
]
trades = [
    ("20261030", "000000", "买入", 100, 14),
    ("20261031", "000000", "卖出", 100, 15),
    ("20261101", "000000", "买入", 1000, 10),
    ("20261102", "000000", "卖出", 400, 9),
    ("20261103", "000000", "买入", 200, 8),
    ("20261104", "000000", "卖出", 100, 15),
    ("20261105", "000000", "买入", 100, 12),
    ("20261105", "000000", "卖出", 100, 12),
    ("20261101", "000001", "买入", 100, 10),
]
rows = [
    {
        "成交日期": d,
        "交收日期": d,
        "业务名称": "证券" + side,
        "证券代码": code,
        "证券名称": "证券零零" if code == "000000" else "证券零一",
        "成交数量": str(q if side == "买入" else -q),
        "成交金额": str(q * price),
        "发生金额": str((-1 if side == "买入" else 1) * q * price),
        "币种": "人民币",
        "流水号": str(i + 1),
        "佣金": "0",
        "印花税": "0",
    }
    for i, (d, code, side, q, price) in enumerate(trades)
]


async def run(root, file):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
            headless=True,
        )
        try:
            page = await browser.new_page(
                viewport={"width": 1440, "height": 900}, reduced_motion="reduce"
            )
            responses = []

            async def collect(r):
                if r.request.method == "POST" and "/stocks/" in r.url and r.ok:
                    responses.append((r.request.post_data_json, await r.json()))

            page.on("response", collect)

            async def waitdetail():
                await page.locator("[data-marker]").first.wait_for(timeout=15000)
                await page.wait_for_timeout(500)

            async def lines():
                return await page.locator("line[data-plan-id]").evaluate_all(
                    '(es)=>es.map(e=>({id:e.dataset.planId,x1:+e.getAttribute("x1"),x2:+e.getAttribute("x2"),y:+e.getAttribute("y1")}))'
                )

            async def markers():
                return await page.locator("[data-marker]").evaluate_all(
                    '(es)=>es.map(e=>({date:e.getAttribute("aria-label").slice(0,10),x:+e.getAttribute("transform").match(/translate\\(([^,]+)/)[1]}))'
                )

            async def anchors(detail):
                ls = await lines()
                ms = await markers()
                plans = {
                    x["id"]: x for s in detail["strategies"] for x in s["expectations"]
                }
                events = {x["id"]: x for x in detail["events"]}
                width_attribute = await page.locator(".chart-overlay").get_attribute("width")
                assert width_attribute is not None
                width = float(width_attribute)
                n = 0
                for line in ls:
                    plan = plans[line["id"]]
                    source = events[plan["source_event_id"]]["date"]
                    mx = [m["x"] for m in ms if m["date"] == source]
                    if mx:
                        assert abs(line["x1"] - mx[0]) < 0.01, (line, source, mx)
                        n += 1
                    if plan["status"] == "completed":
                        end = [m["x"] for m in ms if m["date"] == plan["end_time"][:10]]
                        if end:
                            assert abs(line["x2"] - end[0]) < 0.01, (line, plan, end)
                    else:
                        assert abs(line["x2"] - width) < 0.01, (line, width)
                assert n > 0
                return n

            await page.goto("http://127.0.0.1:18765")
            await page.get_by_label("选择交割单").set_input_files(str(file))
            await page.get_by_role("button", name="分析 1 个文件").click()
            await (
                page.locator("button.security-link")
                .filter(has_text="证券零零")
                .click(timeout=15000)
            )
            await waitdetail()
            detail = responses[-1][1]
            plans = [
                x
                for s in detail["strategies"]
                for x in s["expectations"]
                if x["status"] != "unreliable"
            ]

            async def ids():
                return {x["id"] for x in await lines()}

            assert await ids() == {
                x["id"] for x in plans
                if x["status"] != "completed" and x["strategy"] == "retained_buy_pairing"
            }
            await page.get_by_label("已完成历史").check()
            await page.wait_for_timeout(200)
            allids = await ids()
            assert allids == {x["id"] for x in plans if x["strategy"] == "retained_buy_pairing"}
            await anchors(detail)
            await page.get_by_role("button", name="B 当前批次").click()
            await page.wait_for_timeout(100)
            assert await ids() == {
                x["id"] for x in plans if x["strategy"] == "current_position_lots"
            }
            await page.get_by_role("button", name="A 保留买入").click()
            await page.wait_for_timeout(100)
            assert await ids() == allids
            print("PASS 1", flush=True)
            await page.locator(".chart-shell").scroll_into_view_if_needed()
            await page.wait_for_timeout(200)
            plan = next(x for x in plans if x["id"] in allids)
            await page.get_by_label("期待来源").select_option(plan["id"])
            await page.get_by_role("button", name="查看成交").click()
            event = page.locator('[id="event-' + plan["source_event_id"] + '"]')
            await event.wait_for()
            event_class = await event.get_attribute("class")
            assert event_class is not None
            assert "highlight" in event_class and await event.get_attribute("open") is not None
            print("PASS 2", plan["id"], plan["source_event_id"], flush=True)
            await page.get_by_role("button", name="设置").click()
            slider = page.locator("[role=slider]").nth(0)
            await slider.scroll_into_view_if_needed()
            before = responses[-1][1]
            async with page.expect_response(
                lambda r: (
                    r.request.method == "POST"
                    and "/stocks/" in r.url
                    and ((r.request.post_data_json or {}).get("lots") == 2)
                )
            ) as rp:
                await slider.focus()
                await slider.press("ArrowRight")
            r = await rp.value
            after = await r.json()
            beforeplans = {
                x["id"]: x for s in before["strategies"] for x in s["expectations"]
            }
            afterplans = {
                x["id"]: x for s in after["strategies"] for x in s["expectations"]
            }
            changed = [
                (beforeplans[k], v)
                for k, v in afterplans.items()
                if beforeplans[k]["operation_quantity"] == "100"
                and v["operation_quantity"] == "200"
                and v["strategy"] == "retained_buy_pairing"
            ]
            assert changed and any(
                (
                    a["first_profitable_price"] != b["first_profitable_price"]
                    for a, b in changed
                )
            )
            await page.wait_for_timeout(300)
            assert (
                await page.locator("tbody tr")
                .filter(has_text=f'{float(changed[0][1]["first_profitable_price"]):.2f}')
                .count()
                > 0
            )
            commission = page.locator("[role=slider]").nth(2)
            await commission.focus()
            async with page.expect_response(
                lambda r: (
                    r.request.method == "POST"
                    and "/stocks/" in r.url
                    and ((r.request.post_data_json or {}).get("sell_commission") == 0.003)
                )
            ) as rp:
                await commission.press("End")
            r = await rp.value
            fee = await r.json()
            fp = {x["id"]: x for s in fee["strategies"] for x in s["expectations"]}
            fee_changed = [
                fp[b["id"]]
                for a, b in changed
                if float(b["reference_price"]) * 200 * 0.003 > 5
                and fp[b["id"]]["first_profitable_price"] != b["first_profitable_price"]
            ]
            assert fee_changed
            await page.wait_for_timeout(300)
            assert (
                await page.locator("tbody tr")
                .filter(has_text=f'{float(fee_changed[0]["first_profitable_price"]):.2f}')
                .count()
                > 0
            )
            print("PASS 3", flush=True)
            await page.get_by_role("button", name="设置").click()
            await page.set_viewport_size({"width": 390, "height": 844})
            await page.locator("#theme").select_option("dark")
            chart = page.locator(".chart-shell")
            await chart.scroll_into_view_if_needed()
            await page.wait_for_timeout(500)
            box = await page.locator(".chart").bounding_box()
            assert box is not None
            for i in range(10):
                if len(await markers()) >= 2:
                    break
                await page.mouse.move(
                    box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.6
                )
                await page.mouse.down()
                await page.mouse.move(
                    box["x"] + box["width"] * 0.8,
                    box["y"] + box["height"] * 0.6,
                    steps=10,
                )
                await page.mouse.up()
                await page.wait_for_timeout(200)
            m1 = await markers()
            assert len(m1) >= 2
            await page.mouse.move(
                box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.65
            )
            await page.mouse.down()
            await page.mouse.move(
                box["x"] + box["width"] * 0.4, box["y"] + box["height"] * 0.65, steps=10
            )
            await page.mouse.up()
            await page.wait_for_timeout(300)
            m2 = await markers()
            delta = [
                next((b["x"] - a["x"] for b in m2 if b["date"] == a["date"]), None)
                for a in m1
            ]
            delta = [x for x in delta if x is not None]
            assert (
                len(delta) >= 2
                and abs(delta[0] - delta[1]) < 0.02
                and (abs(delta[0]) > 1)
            )
            await anchors(fee)
            await chart.screenshot(path=str(root / "dark-390.png"))
            await page.mouse.move(
                box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5
            )
            await page.mouse.wheel(0, -150)
            await page.wait_for_timeout(400)
            await anchors(fee)
            await chart.screenshot(path=str(root / "dark-390-scroll.png"))
            print("PASS 4", flush=True)
            await page.set_viewport_size({"width": 1440, "height": 900})
            if await page.get_by_role("button", name="← 收益报告").count():
                await page.get_by_role("button", name="← 收益报告").click()
            started = asyncio.Event()
            finished = asyncio.Event()
            outcomes = []

            async def delay(route):
                if (
                    "/stocks/" in route.request.url
                    and "000000" in route.request.url
                    and (not started.is_set())
                ):
                    response = await route.fetch()
                    started.set()
                    await asyncio.sleep(2)
                    try:
                        await route.fulfill(response=response)
                        outcomes.append("fulfilled delayed response")
                    except PlaywrightError as e:
                        outcomes.append("aborted: " + str(e).splitlines()[0])
                    finished.set()
                else:
                    await route.continue_()

            await page.route("**/api/sessions/**/stocks/**", delay)
            await (
                page.locator("button.security-link").filter(has_text="证券零零").click()
            )
            await asyncio.wait_for(started.wait(), 10)
            await page.get_by_role("button", name="← 收益报告").click()
            await (
                page.locator("button.security-link").filter(has_text="证券零一").click()
            )
            await page.get_by_role("heading", name="证券零一 SZ:000001").wait_for(
                timeout=10000
            )
            await asyncio.wait_for(finished.wait(), 10)
            await page.wait_for_timeout(500)
            assert await page.get_by_role(
                "heading", name="证券零一 SZ:000001"
            ).is_visible()
            assert (
                await page.get_by_role("heading", name="证券零零 SZ:000000").count()
                == 0
            )
            assert await page.locator(".event").count() == 1
            print("PASS 5", outcomes, flush=True)
        finally:
            await browser.close()


def main():
    with tempfile.TemporaryDirectory(prefix="stock-accept-") as temporary:
        root = Path(temporary)
        file = write_statement(root / "anonymous.txt", columns, rows)
        app = create_app(market_cache=MarketCache(root / "cache", fetcher=fetch))
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=18765, log_level="error")
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 15
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError(
                        "Acceptance server failed to start on port 18765"
                    )
                time.sleep(0.05)
            asyncio.run(run(root, file))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                raise RuntimeError("Acceptance server did not stop")


if __name__ == "__main__":
    main()
