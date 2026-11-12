import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Slider } from "@/components/ui/slider";
import { Badge } from "@/components/ui/badge";
import { Popover, Tooltip } from "radix-ui";
import {
  Table,
  TableHeader,
  TableHead,
  TableBody,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import {
  api,
  shown,
  type Session,
  type Report,
  type Detail,
  type Market,
  type Expectation,
  type Quote,
} from "./api";
import TradeChart from "./TradeChart";
import About from "./About";
import { version } from "../package.json";
const A = "retained_buy_pairing";
const B = "current_position_lots";
const price = (value: string | number | null | undefined, digits: number) =>
  value == null ? "—" : Number(value).toFixed(digits);
const signed = (value: string | number, digits: number) => `${Number(value) > 0 ? "+" : ""}${Number(value).toFixed(digits)}`;
const profitTone = (value: string | number | null | undefined) =>
  Number.parseFloat(String(value)) > 0 ? "gain" : Number.parseFloat(String(value)) < 0 ? "loss" : "";
const initialFees = {
  buy_commission: 0.0001,
  sell_commission: 0.0001,
  stamp_tax: 0.0005,
  transfer: 0.00001,
  lots: 1,
  buy_minimum: true,
  sell_minimum: true,
};
function readFees() {
  try {
    const saved = JSON.parse(localStorage.getItem("settings") || "{}");
    const result = { ...initialFees };
    for (const key of ["buy_commission", "sell_commission", "stamp_tax", "transfer"] as const)
      if (typeof saved[key] === "number" && saved[key] >= 0 && saved[key] <= 0.003) result[key] = saved[key];
    if (Number.isInteger(saved.lots) && saved.lots >= 1 && saved.lots <= 100) result.lots = saved.lots;
    for (const key of ["buy_minimum", "sell_minimum"] as const)
      if (typeof saved[key] === "boolean") result[key] = saved[key];
    return result;
  } catch { return { ...initialFees }; }
}
const dateOnly = (value: string) => value.replace(/^(\d{4})(\d{2})(\d{2})$/, "$1-$2-$3");
const dateTime = (value: Date) => `${value.getFullYear()}-${String(value.getMonth()+1).padStart(2,"0")}-${String(value.getDate()).padStart(2,"0")} ${String(value.getHours()).padStart(2,"0")}:${String(value.getMinutes()).padStart(2,"0")}:${String(value.getSeconds()).padStart(2,"0")}`;
function eventDate(date: string, time: string | null) {
  const day = dateOnly(date);
  if (!time) return day;
  const clock = time.replace(/^(\d{2})(\d{2})(\d{2})$/, "$1:$2:$3");
  const parsed = new Date(`${day}T${clock}+08:00`);
  return Number.isNaN(parsed.getTime()) ? `${day} ${time}` : dateTime(parsed);
}
function ErrorNotice({ message }: { message: string }) {
  return (
    <p role="alert" className="notice error">
      {message}
    </p>
  );
}
function Loading({ children }: { children: React.ReactNode }) {
  return (
    <p role="status" className="notice loading">
      {children}
    </p>
  );
}
export default function App() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem("theme") || "system",
  );
  const [dark, setDark] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [stock, setStock] = useState("");
  const [files, setFiles] = useState<{ file: File; account: string }[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    const update = () => {
      const value = theme === "dark" || (theme === "system" && media.matches);
      setDark(value);
      document.documentElement.classList.toggle("dark", value);
    };
    update();
    media.addEventListener("change", update);
    localStorage.setItem("theme", theme);
    return () => media.removeEventListener("change", update);
  }, [theme]);
  async function clear() {
    if (session) await api(`/api/sessions/${session.id}`, { method: "DELETE" });
    setSession(null);
    setReport(null);
    setStock("");
    setFiles([]);
    setError("");
    if (fileInput.current) fileInput.current.value = "";
  }
  function addFiles(incoming: FileList | null) {
    if (incoming) {
      setError("");
      setFiles((previous) => [
        ...previous,
        ...Array.from(incoming).map((file) => ({
          file,
          account: file.name.match(/招商证券|东方财富/)?.[0] ?? "默认账户",
        })),
      ]);
    }
  }
  async function upload() {
    setBusy(true);
    setError("");
    try {
      if (
        files.some((item) => item.file.size > 8 * 1024 * 1024) ||
        files.reduce((sum, item) => sum + item.file.size, 0) > 24 * 1024 * 1024
      )
        throw new Error("单文件上限 8 MiB，总量上限 24 MiB");
      const payload = await Promise.all(
        files.map(async ({ file, account }) => {
          const bytes = new Uint8Array(await file.arrayBuffer());
          let binary = "";
          for (let i = 0; i < bytes.length; i += 8192)
            binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
          return { name: file.name, account, data: btoa(binary) };
        }),
      );
      const created = await api<Session>("/api/sessions", {
        method: "POST",
        body: JSON.stringify({ files: payload }),
      });
      setSession(created);
      setFiles([]);
      setReport(await api<Report>(`/api/sessions/${created.id}/report`));
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="app">
      <header className="topbar">
        <a
          href="#"
          onClick={(e) => {
            e.preventDefault();
            setStock("");
          }}
          className="brand"
        >
          交割单 <span>交易计划</span>
        </a>
        <div className="flex gap-3 items-center">
          <About />
          <label className="sr-only" htmlFor="theme">
            主题
          </label>
          <select
            id="theme"
            value={theme}
            onChange={(e) => setTheme(e.target.value)}
          >
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
          {session && (
            <Button
              variant="outline"
              onClick={() => void clear().catch((e) => setError(e.message))}
            >
              清空本次会话
            </Button>
          )}
        </div>
      </header>
      {error && <ErrorNotice message={error} />}
      {!session && (
        <section className="page">
          <div className="intro">
            <p className="eyebrow">TRADE PLANNER</p>
            <h1>从交割单，看下一笔交易。</h1>
            <p className="muted">
              逐笔还原买卖，把未来的价格条件与可操作数量放在一起。
            </p>
          </div>
          <div
            className="dropzone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (!busy) addFiles(e.dataTransfer.files);
            }}
          >
            <svg className="dropzone-icon" viewBox="0 0 120 120" fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true"><path d="M30 14h40l22 22v70H30zM70 14v24h22M43 55h35M43 69h25M43 83h35"/><path d="M15 45v65h55"/></svg>
            <h2>拖放交割单到这里</h2>
            <p className="muted">
              招商证券 / 东方财富 · TXT · UTF-8 或 GB18030 · 可多选
            </p>
            <input
              ref={fileInput}
              className="sr-only"
              type="file"
              multiple
              accept=".txt"
              aria-label="选择交割单"
              disabled={busy}
              onChange={(e) => addFiles(e.target.files)}
            />
            <div className="upload-actions"><Button size="lg" disabled={busy} onClick={() => fileInput.current?.click()}>选择交割单</Button><span className="muted text-xs">每份 ≤ 8 MiB · 总计 ≤ 24 MiB</span></div>
          </div>
          {!!files.length && (
            <Card>
              <CardHeader>
                <CardTitle>确认账户范围</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="muted">
                  同一账户的分段文件使用相同标签；同券商不同账户必须使用不同标签。原始格式无法可靠提供账户号。
                </p>
                {files.map((item, index) => (
                  <div className="file-row" key={index}>
                    <span>{item.file.name}</span>
                    <Input
                      aria-label={`${item.file.name} 账户标签`}
                      value={item.account}
                      onChange={(e) =>
                        setFiles(
                          files.map((f, i) =>
                            i === index ? { ...f, account: e.target.value } : f,
                          ),
                        )
                      }
                    />
                    <Button
                      variant="ghost"
                      disabled={busy}
                      onClick={() => {
                        setError("");
                        setFiles(files.filter((_, i) => i !== index));
                      }}
                    >
                      移除
                    </Button>
                  </div>
                ))}
                <Button
                  disabled={busy || files.some((f) => !f.account.trim())}
                  onClick={upload}
                >
                  {busy ? "解析与核算中…" : `分析 ${files.length} 个文件`}
                </Button>
              </CardContent>
            </Card>
          )}
          {busy && (
            <Loading>正在解码、去重并核算…</Loading>
          )}
        </section>
      )}
      {session && !stock && report && (
        <section className="page">
          <div className="page-heading">
            <div>
              <p className="eyebrow">EARNINGS</p>
              <h1>历史收益报告</h1>
            </div>
            <Badge variant="outline">{session.accounts.join(" / ")}</Badge>
          </div>
          <p className="muted">
            {dateOnly(String(report.metadata.start_date))}—
            {dateOnly(String(report.metadata.end_date))} ·{" "}
            {String(report.metadata.files_count)} 个文件 ·{" "}
            {String(report.metadata.entries_count)} 条流水 · 去重{" "}
            {String(report.metadata.duplicates)} 条
          </p>
          {report.incomplete && (
            <ErrorNotice message="报告不完整：未知业务或成本缺失；— 表示无法确定，合计仅包含已知项。" />
          )}
          <div>
            <h2>账户概览</h2>
            <div className="overview-grid">
              {report.overview.filter((row) => row.parent === null).map((row) => (
                <div className="overview-group" key={row.key}>
                  <div className="overview-main">
                    <span className="muted">{row.title}</span>
                    <OverviewValue row={row} />
                  </div>
                  {report.overview.filter((child) => child.parent === row.key && child.key !== "REPO_FEES").map((child) => (
                    <div className="overview-child" key={child.key}>
                      <span className="muted">{child.title.replace("交易总额", "")}</span>
                      <OverviewValue row={child} />
                    </div>
                  ))}
                </div>
              ))}
            </div>
            {(report.metadata.notes as string[]).map((note) => (
              <p key={note} className="muted">
                {note}
              </p>
            ))}
          </div>
          <Card>
            <CardHeader>
              <CardTitle>
                证券核算{" "}
                <span className="muted text-sm">
                  点击股票查看价格与交易计划
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    {report.columns.map((c) => (
                      <TableHead
                        key={c.key}
                        className={c.numeric ? "text-right" : ""}
                      >
                        {c.title}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {[...report.rows, report.totals].map((row, i) => {
                    const security = session.securities.find(
                      (s) => s.code === row.code,
                    );
                    return (
                      <TableRow
                        key={i}
                        className={i === report.rows.length ? "total" : ""}
                      >
                        {report.columns.map((c) => (
                          <TableCell
                            key={c.key}
                            className={`${c.numeric ? "number" : ""} ${c.change ? profitTone(row[c.key]) : ""}`}
                          >
                            {c.key === "name" && security ? (
                              <Button
                                variant="link"
                                className="security-link"
                                onClick={() => setStock(security.id)}
                              >
                                {shown(row[c.key])} ↗
                              </Button>
                            ) : (
                              c.key === "name" && /^(204|1318)/.test(String(row.code)) ? (
                                <Tooltip.Provider><Tooltip.Root>
                                  <Tooltip.Trigger asChild><span tabIndex={0} className="cursor-help">{shown(row[c.key])}</span></Tooltip.Trigger>
                                  <Tooltip.Portal><Tooltip.Content className="security-tooltip" sideOffset={6}>逆回购不提供交易计划详情<Tooltip.Arrow /></Tooltip.Content></Tooltip.Portal>
                                </Tooltip.Root></Tooltip.Provider>
                              ) : (
                                shown(row[c.key])
                              )
                            )}
                          </TableCell>
                        ))}
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          {report.rows.length === 0 && (
            <p className="notice">没有证券交易，可查看账户资金概览。</p>
          )}

          {!!report.unknown.length && (
            <details>
              <summary>待确认流水</summary>
              {report.unknown.map((row, i) => (
                <p key={i}>
                  {String(row.source)} 第 {String(row.line)} 行 ·{" "}
                  {String(row.business)} · {String(row.code)}
                </p>
              ))}
            </details>
          )}
        </section>
      )}
      {session && stock && (
        <StockPage
          key={stock}
          session={session}
          stock={stock}
          startDate={String(report?.metadata.start_date ?? "19900101")}
          dark={dark}
          onBack={() => setStock("")}
        />
      )}
      <footer>
        <span>© 2026 九条涼果 · MIT License</span><span>v{version}</span>
      </footer>
    </div>
  );
}

function StockPage({
  session,
  stock,
  startDate,
  dark,
  onBack,
}: {
  session: Session;
  stock: string;
  startDate: string;
  dark: boolean;
  onBack: () => void;
}) {
  const security = session.securities.find((s) => s.id === stock)!;
  const [account, setAccount] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [market, setMarket] = useState<Market | null>(null);
  const [marketLoading, setMarketLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [marketError, setMarketError] = useState("");
  const [fees, setFees] = useState(readFees);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteRetry, setQuoteRetry] = useState(0);
  const [quoteLoading, setQuoteLoading] = useState(true);
  const [candidate, setCandidate] = useState(() => {
    const value = localStorage.getItem(`candidate:${stock}`) || "";
    return value && Number.isFinite(Number(value)) && Number(value) >= 0 ? value : "";
  });
  const [strategy, setStrategy] = useState(A);
  const [history, setHistory] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [openEvents, setOpenEvents] = useState<string[]>([]);
  const [retry, setRetry] = useState(0);
  const latestClose = market?.candles.at(-1)?.close ?? null;
  const priceDigits = /^(5|1[568])/.test(security.code) ? 3 : 2;
  const showPrice = (value: string | null | undefined) => price(value, priceDigits);
  useEffect(() => { window.scrollTo(0, 0); }, []);
  useEffect(() => { localStorage.setItem("settings", JSON.stringify(fees)); }, [fees]);
  useEffect(() => { localStorage.setItem(`candidate:${stock}`, candidate); }, [stock, candidate]);
  useEffect(() => {
    const controller = new AbortController();
    setQuoteLoading(true);
    api<Quote>(`/api/quotes/${security.market}/${security.code}`, { signal: controller.signal }).then(setQuote).catch(() => {
      if (!controller.signal.aborted) setQuote({market: security.market, code: security.code, price: null, previous_close: null, change: null, change_percent: null, updated_at: null, source: "暂无数据", error: "实时价格暂不可用"});
    }).finally(() => { if (!controller.signal.aborted) setQuoteLoading(false); });
    return () => controller.abort();
  }, [security.market, security.code, quoteRetry]);
  useEffect(() => {
    const controller = new AbortController();
    setMarketLoading(true);
    setMarketError("");
    api<Market>(
      `/api/market/${security.market}/${security.code}?start=${startDate}`,
      { signal: controller.signal },
    )
      .then(setMarket)
      .catch((e) => {
        if (!controller.signal.aborted) setMarketError(e.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setMarketLoading(false);
      });
    return () => controller.abort();
  }, [security.market, security.code, startDate, retry]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      api<Detail>(`/api/sessions/${session.id}/stocks/${stock}`, {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          ...fees,
          account: account || null,
          price: quote?.price ?? null,
          candidate: Number(candidate) > 0 ? candidate : null,
        }),
      })
        .then((value) => {
          setDetail(value);
          setError("");
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 120);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [session.id, stock, fees, account, quote?.price, candidate]);
  function select(ids: string[], scrollTo = ids[0]) {
    setSelected(ids);
    setOpenEvents(ids);
    requestAnimationFrame(() =>
      document
        .getElementById(`event-${scrollTo}`)
        ?.scrollIntoView({
          behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
            ? "instant"
            : "smooth",
          block: "center",
        }),
    );
  }
  const strategies =
    detail?.strategies.filter((s) => s.strategy === strategy) ?? [];
  const chartPlans = strategies
    .flatMap((s) => s.expectations)
    .filter(
      (p) => p.status !== "unreliable" && (history || p.status !== "completed"),
    );
  const eventTimes = new Map(detail?.events.map((event) => [event.id, `${event.date} ${event.time ?? ""}`]));
  const plans = chartPlans.filter((p) => p.status === "open" && Number(p.plan_quantity) > 0).sort((a, b) => (eventTimes.get(b.source_event_id) ?? b.start_time).localeCompare(eventTimes.get(a.source_event_id) ?? a.start_time));
  const showAccount = new Set(detail?.events.map((event) => event.account)).size > 1;
  const quoteTone = Number(quote?.change ?? 0) > 0 ? "gain" : Number(quote?.change ?? 0) < 0 ? "loss" : "";
  return (
    <section className="page">
      <div className="page-heading">
        <div>
          <Button variant="ghost" onClick={onBack}>
            ← 收益报告
          </Button>
          <h1>
            {security.name} <a className="muted text-lg" href={`https://quote.eastmoney.com/concept/${security.market.toLowerCase()}${security.code}.html`} target="_blank" rel="noreferrer">{security.code} ↗</a>
          </h1>
        </div>
        <label>
          账户范围{" "}
          <select
            aria-label="账户范围"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          >
            <option value="">全部账户（独立计算）</option>
            {session.accounts.map((a) => (
              <option key={a}>{a}</option>
            ))}
          </select>
        </label>
      </div>
      {error && <ErrorNotice message={error} />}
      {loading && !detail && <div className="detail-skeleton" role="status" aria-label="正在加载详情"><div className="skeleton-switch" /><div className="skeleton-dashboard" /><div className="skeleton-chart" /><div className="skeleton-table" /></div>}
      {detail && (
        <>
          <Card className="security-summary">
            <CardContent>
              <div className="summary-quote">
                <div className="quote-heading"><span className="muted">现价</span><Button variant="ghost" size="sm" disabled={quoteLoading} aria-label={quoteLoading ? "正在刷新现价" : "刷新现价"} onClick={() => { setQuoteLoading(true); setQuoteRetry((value) => value + 1); }}><svg className={quoteLoading ? "refresh-icon spinning" : "refresh-icon"} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5M5.2 8a7.5 7.5 0 0 1 12.4-3L20 8M4 16l2.4 3A7.5 7.5 0 0 0 18.8 16"/></svg>{quoteLoading ? "刷新中" : "刷新"}</Button></div>
                <strong className={quoteTone} aria-busy={quoteLoading}>{showPrice(quote?.price)}</strong>
                {quote?.change != null && <div className={`quote-change ${quoteTone}`}><span>{signed(quote.change, priceDigits)}</span>{quote.change_percent != null && <span>{signed(quote.change_percent, 2)}%</span>}</div>}
                <small className="muted">{quote?.updated_at ? `${dateTime(new Date(quote.updated_at * 1000))} · ${quote.source}` : quote?.error ? "报价暂不可用" : "等待报价"}</small>
              </div>
              {detail.strategies.filter((s) => s.strategy === A).map((s) => (
                <div className="summary-holding" key={s.account}>
                  <span className="muted">{s.account} · 真实持仓</span>
                  <strong>{s.quantity} <small>股</small></strong>
                  <div className="summary-details">
                    <Metric label="持仓成本" value={price(s.holding_cost, 2)} />
                    <Metric label="持仓市值" value={price(s.holding_market_value, 2)} />
                    <Metric label="浮盈" value={price(s.holding_profit, 2)} tone />
                    <Metric label="浮盈率" value={s.holding_profit_rate == null ? null : `${price(s.holding_profit_rate, 2)}%`} tone />
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
          <div className="strategy-switch" role="group" aria-label="交易策略">
            <Button variant={strategy === A ? "default" : "outline"} onClick={() => setStrategy(A)}>A 保留买入</Button>
            <Button variant={strategy === B ? "default" : "outline"} onClick={() => setStrategy(B)}>B 当前批次</Button>
            <span className="muted">{strategy === A ? "保留尚未盈利配对的买入" : "卖出后扣减对应持仓批次"}</span>
          </div>
          <div className="dashboard">
            {strategies.map((s) => (
              <Card key={`${s.account}:${s.strategy}`}>
                <CardHeader>
                  <div className="flex justify-between gap-2">
                    <CardTitle>{s.name}</CardTitle>
                    <Badge variant="outline">{s.account}</Badge>
                  </div>
                </CardHeader>
                <CardContent>
                  {s.incomplete && (
                    <ErrorNotice message="策略不完整：以下归因仅包含已知交易，已隐藏期待价位。" />
                  )}
                  <div className="metrics">
                    <Metric label="持仓均价 · 含买入费用" value={showPrice(s.holding_average_cost)} />
                    <Metric
                      label={
                        s.strategy === A ? "未完成买入均价" : "当前持仓批次均价"
                      }
                      value={showPrice(s.average_cost)}
                    />
                    <Metric
                      label={
                        s.strategy === A ? "已完成配对价差 · 未计费用" : "已实现批次价差 · 未计费用"
                      }
                      value={price(s.realized, 2)}
                      tone
                    />
                    <Metric
                      label="总经济盈亏"
                      value={price(s.economic_profit, 2)}
                      tone
                      difference={s.economic_profit == null ? null : Number(s.economic_profit) - Number(s.realized)}
                      base={Number(s.realized)}
                    />
                  </div>
                  <div className="next-targets">
                    {["sell", "buy"].map((side) => {
                      const targets = s.expectations
                        .filter((p) => p.side === side && p.status === "open" && Number(p.plan_quantity) > 0)
                        .sort((a, b) =>
                          side === "sell"
                            ? Number(a.reference_price) -
                              Number(b.reference_price)
                            : Number(b.reference_price) -
                              Number(a.reference_price),
                        );
                      const target = targets[0];
                      return (
                        <p
                          key={side}
                          className={side === "buy" ? "buy" : "sell"}
                        >
                          {side === "sell" ? "下一档卖出" : "下一档回补"}：
                          {target
                            ? `${target.comparison} ${showPrice(target.reference_price)} · ${target.plan_quantity} 股`
                            : "无未完成期待"}
                        </p>
                      );
                    })}
                  </div>
                  {s.incomplete && s.warnings.map((w) => <p key={w} className="error notice">{w}</p>)}
                </CardContent>
              </Card>
            ))}
          </div>
          <Card>
            <CardHeader>
              <div className="page-heading">
                <div>
                  <CardTitle>不复权日 K 与逐笔期待</CardTitle>
                  <p className="muted">
                    {latestClose
                      ? `${market?.candles.at(-1)?.time} 历史收盘价 ${showPrice(latestClose)} · ${market?.source}${market?.provisional ? " · 当日日线暂定" : ""}`
                      : marketLoading ? "正在加载行情…" : "暂无可用收盘价；策略与交易计划仍可查看"}
                  </p>
                </div>
                <div className="flex gap-3 flex-wrap">
                  <label>
                    <input
                      type="checkbox"
                      checked={history}
                      onChange={(e) => setHistory(e.target.checked)}
                    />{" "}
                    已完成历史
                  </label>
                </div>
              </div>
            </CardHeader>
            <CardContent>
              {marketLoading && !market && <div className="chart-skeleton" role="status" aria-label="正在加载行情" />}
              {(marketError || market?.error) && (
                <div>
                  <ErrorNotice message={marketError || market?.error || ""} />
                  <Button variant="outline" onClick={() => setRetry(retry + 1)}>
                    重试行情
                  </Button>
                </div>
              )}
              {market && (
                <p className="muted text-xs">
                  {market.stale ? "陈旧缓存" : "公共行情缓存"} · 更新时间{" "}
                  {market.updated_at
                    ? dateTime(new Date(market.updated_at * 1000))
                    : "无"}{" "}
                  · {market.adjustment}
                </p>
              )}
              {market && market.candles.length > 0 ? (
                  <TradeChart
                    market={market}
                    markers={detail.markers}
                    plans={chartPlans}
                    events={detail.events}
                    priceDigits={priceDigits}
                    dark={dark}
                    selected={selected}
                    onSelect={select}
                  />
              ) : (
                !marketLoading && (
                  <p className="notice">行情暂无数据，不展示虚构 K 线。</p>
                )
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <div className="page-heading">
                <CardTitle>未来价格 — 交易数量</CardTitle>
                <Popover.Root>
                  <Popover.Trigger asChild><Button variant="outline">设置</Button></Popover.Trigger>
                  <Popover.Portal>
                    <Popover.Content className="settings-popover" align="end" sideOffset={8}>
                      <div className="settings-grid">
                        <label>候选卖价
                          <Input aria-label="候选卖价" type="number" min="0" step={priceDigits === 3 ? "0.01" : "0.1"} value={candidate} onChange={(e) => setCandidate(e.target.value)} />
                        </label>
                        <label>每次操作 {fees.lots} 手 / {fees.lots * 100} 股
                          <Input aria-label="操作手数输入" type="number" min={1} max={100} step={1} value={fees.lots} onChange={(e) => { const n = Number(e.target.value); if (Number.isInteger(n) && n >= 1 && n <= 100) setFees({ ...fees, lots: n }); }} />
                          <Slider aria-label="操作手数" value={[fees.lots]} min={1} max={100} step={1} onValueChange={([value]) => setFees({ ...fees, lots: value })} />
                        </label>
                        {(["buy_commission", "sell_commission", "stamp_tax", "transfer"] as const).map((key, index) => (
                          <div className="fee-setting" key={key}>
                            <div className="fee-heading">
                              <label htmlFor={`fee-${key}`}>{["买入佣金", "卖出佣金", "印花税", "过户费"][index]}</label>
                              {index < 2 && <label className="minimum-fee"><input type="checkbox" aria-label={`${index === 0 ? "买入" : "卖出"}佣金最低5元`} checked={fees[index === 0 ? "buy_minimum" : "sell_minimum"]} onChange={(e) => setFees({ ...fees, [index === 0 ? "buy_minimum" : "sell_minimum"]: e.target.checked })} />最低 5 元</label>}
                            </div>
                            <div className="fee-input"><Input id={`fee-${key}`} aria-label={`${["买入佣金", "卖出佣金", "印花税", "过户费"][index]}输入`} type="number" min={0} max={0.3} step={0.001} value={Number((fees[key] * 100).toFixed(4))} onChange={(e) => { const n = Number(e.target.value) / 100; if (n >= 0 && n <= 0.003) setFees({ ...fees, [key]: n }); }} /><span>%</span></div>
                            <Slider aria-label={["买入佣金", "卖出佣金", "印花税", "过户费"][index]} value={[fees[key]]} min={0} max={0.003} step={0.00001} onValueChange={([value]) => setFees({ ...fees, [key]: value })} />
                          </div>
                        ))}
                      </div>
                    </Popover.Content>
                  </Popover.Portal>
                </Popover.Root>
              </div>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    {[
                      "账户",
                      "方向",
                      "来源日期",
                      "参考条件",
                      "剩余 / 原始数量",
                      "现价可操作",
                      "含费用首个盈利价",
                      "操作量",
                      "来源 / 状态",
                    ].map((x, index) => (
                      <TableHead key={x} className={index >= 3 && index <= 7 ? "number" : ""}>{x}</TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {plans.map((p) => (
                    <PlanRow
                      key={p.id}
                      plan={p}
                      showPrice={showPrice}
                      showCandidate={Number(candidate) > 0}
                      selected={selected.includes(p.source_event_id)}
                      onSelect={() => select([p.source_event_id])}
                    />
                  ))}
                </TableBody>
              </Table>
              {!plans.length && (
                <p className="notice">暂无未完成的交易计划。</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>逐笔交易</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="events-scroll"><div className="events-table">
              <div className={`event-columns muted ${showAccount ? "with-account" : ""}`}><span>日期</span><span>方向</span><span>数量</span><span>价格</span><span>净现金</span><span>手续费</span>{showAccount && <span>账户</span>}</div>
              {[...detail.events].reverse().sort((a, b) => b.date.localeCompare(a.date) || (b.time ?? "").localeCompare(a.time ?? "")).map((event) => (
                <details
                  id={`event-${event.id}`}
                  key={event.id}
                  open={openEvents.includes(event.id)}
                  className={`event ${showAccount ? "with-account" : ""} ${selected.includes(event.id) ? event.business === "证券买入" ? "highlight-buy" : event.business === "证券卖出" ? "highlight-sell" : "highlight-buy" : ""}`}
                >
                  <summary onClick={(e) => { e.preventDefault(); setOpenEvents((previous) => previous.includes(event.id) ? previous.filter((id) => id !== event.id) : [...previous, event.id]); }}>
                    <span>
                      {eventDate(event.date, event.time)}
                    </span>
                    <span
                      className={
                        event.business === "证券买入"
                          ? "buy"
                          : event.business === "证券卖出"
                            ? "sell"
                            : ""
                      }
                    >
                      {event.business}
                    </span>
                    <span>
                      {shown(event.quantity)}
                    </span>
                    <span>{showPrice(event.price)}</span>
                    <span>{price(event.amount, 2)}</span>
                    <span>{price(Object.values(event.fees).reduce((total, fee) => total + Number(fee), 0), 2)}</span>
                    {showAccount && <span className="muted event-account" title={event.account}>{event.account}</span>}
                  </summary>
                  <div className="event-body">
                    <p>
                      {event.source} · 第 {event.line} 行 · {event.platform}
                    </p>
                    <p className="muted">
                      明列费用：
                      {Object.entries(event.fees)
                        .map(([key, value]) => `${key} ${price(value, 2)}`)
                        .join(" / ")}
                    </p>
                    {strategies
                      .filter((s) => s.account === event.account)
                      .map((s) => (
                        <div key={s.strategy}>
                          {s.pairs
                            .filter(
                              (p) =>
                                p.source_event_id === event.id ||
                                p.target_event_id === event.id,
                            )
                            .map((p, i) => (
                              <p key={i}>
                                {p.kind === "replenishment"
                                  ? "回补改善（不再计收益）"
                                  : "配对价差"}{" "}
                                <span className={profitTone(price(p.profit, 2))}>{price(p.profit, 2)}</span> · {p.quantity} 股{" "}
                                <Button
                                  variant="link"
                                  onClick={() => select([p.source_event_id, p.target_event_id], event.id === p.source_event_id ? p.target_event_id : p.source_event_id)}
                                >
                                  定位双方
                                </Button>
                              </p>
                            ))}
                          {s.expectations
                            .filter((p) => p.source_event_id === event.id)
                            .map((p) => (
                              <p key={p.id}>
                                {p.side === "sell" ? "卖出期待" : "回补期待"}{" "}
                                {p.comparison}
                                {showPrice(p.reference_price)} · 已配 {p.matched_quantity}{" "}
                                / 原始 {p.original_quantity}；数量历史：
                                {p.changes
                                  .map((c) => `${c.time} → ${c.quantity}`)
                                  .join("；")}
                              </p>
                            ))}
                        </div>
                      ))}
                  </div>
                </details>
              ))}
              </div></div>
            </CardContent>
          </Card>
        </>
      )}
    </section>
  );
}
function Metric({ label, value, tone = false, difference, base }: { label: string; value: string | null; tone?: boolean; difference?: number | null; base?: number }) {
  return (
    <div>
      <span className="muted">{label}</span>
      <div className="metric-value"><strong className={tone ? profitTone(value) : ""}>
        {shown(value)}
      </strong>
      {difference != null && <small className={`metric-difference ${difference < 0 ? "loss" : difference > 0 ? "gain" : "muted"}`} title="相对已实现价差，含费用和账本调整；差额 ÷ 已实现价差绝对值"><span>{signed(difference, 2)}</span>{base != null && base !== 0 && <span>{signed(difference / Math.abs(base) * 100, 2)}%</span>}</small>}</div>
    </div>
  );
}
function PlanRow({
  plan: p,
  showPrice,
  showCandidate,
  selected,
  onSelect,
}: {
  plan: Expectation;
  showCandidate: boolean;
  showPrice: (value: string | null | undefined) => string;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <TableRow className={`${selected ? "highlight" : ""} ${Number(p.current_executable_quantity) > 0 ? "plan-actionable" : ""}`}>
      <TableCell>
        {p.account}
      </TableCell>
      <TableCell className={p.side === "buy" ? "buy" : "sell"}>
        {p.side === "buy" ? "买回" : "卖出"}
      </TableCell>
      <TableCell>{dateOnly(p.start_time)}</TableCell>
      <TableCell className="number">
        {p.comparison} {showPrice(p.reference_price)}
      </TableCell>
      <TableCell className="number">
        {p.plan_quantity} / {p.original_quantity}
      </TableCell>
      <TableCell className="number" title={p.limitation}>
        {Number(p.current_executable_quantity) > 0 ? <span className="actionable-quantity" title={p.side === "buy" ? "已达到含费用盈利价，需自行确认可用资金" : "已达到含费用盈利价，以交割单持仓为限"}><span className="status-dot" />{p.current_executable_quantity}<small>{p.side === "buy" ? "可买回" : "可卖出"}</small></span> : shown(p.current_executable_quantity)}
        {showCandidate && p.side === "sell" && <small className="candidate-result muted" title="候选卖价下可执行数量 / 累计数量">候选价 {shown(p.executable_quantity)} / {shown(p.cumulative_executable_quantity)}</small>}
      </TableCell>
      <TableCell className="number">
        {showPrice(p.first_profitable_price)}
      </TableCell>
      <TableCell className="number">{p.operation_quantity}</TableCell>
      <TableCell>
        <Button variant="link" onClick={onSelect}>
          {p.status === "completed" ? "已完成" : "定位成交"}
        </Button>
      </TableCell>
    </TableRow>
  );
}

function OverviewValue({ row }: { row: Report["overview"][number] }) {
  const rates = row.note.match(/[^；]*[+-]?\d+(?:\.\d+)?[%‰]/g) ?? [];
  const note = row.note.split("；").filter((part) => !/\d[%‰]/.test(part)).join("；");
  return <div className="overview-value"><strong className={row.change ? profitTone(row.amount) : `overview-${row.tone}`}>{row.amount}</strong>{rates.length > 0 && <div className="overview-rates">{rates.map((rate) => <small key={rate} title={rate} className={row.change ? profitTone(rate.match(/[+-]?\d+(?:\.\d+)?/)?.[0]) : ""}>{rate}</small>)}</div>}{note && <small className="muted overview-note">{note}</small>}</div>;
}
