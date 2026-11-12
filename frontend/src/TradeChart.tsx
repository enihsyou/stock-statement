import { useEffect, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  ColorType,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { Button } from "@/components/ui/button";
import type { Detail, Expectation, Market, Marker } from "./api";

type Props = {
  market: Market;
  markers: Marker[];
  plans: Expectation[];
  events: Detail["events"];
  priceDigits: number;
  dark: boolean;
  selected: string[];
  onSelect: (ids: string[]) => void;
};
type Layout = {
  width: number;
  height: number;
  markers: { marker: Marker; x: number; y: number }[];
  lines: { plan: Expectation; x: number; end: number; y: number }[];
};
export default function TradeChart({
  market,
  markers,
  plans,
  events,
  priceDigits,
  dark,
  selected,
  onSelect,
}: Props) {
  const element = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const latest = useRef({ market, markers, plans });
  latest.current = { market, markers, plans };
  const redraw = useRef<() => void>(() => {});
  const [layout, setLayout] = useState<Layout>({
    width: 0,
    height: 0,
    markers: [],
    lines: [],
  });
  const [chosen, setChosen] = useState("");
  const [hover, setHover] = useState<Marker | null>(null);
  const [candle, setCandle] = useState<Market["candles"][number] | null>(null);
  useEffect(() => {
    const host = element.current!;
    const instance = createChart(host, {
      autoSize: true,
      height: 430,
      layout: { attributionLogo: true, fontFamily: 'Inter, sans-serif' },
      rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0.15 } },
      timeScale: { rightOffset: 8, borderVisible: false },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true },
    });
    const series = instance.addSeries(CandlestickSeries, {
      upColor: "#df434d",
      downColor: "#159669",
      wickUpColor: "#df434d",
      wickDownColor: "#159669",
      borderVisible: false,
      priceFormat: { type: "price", precision: priceDigits, minMove: 10 ** -priceDigits },
    });
    chart.current = instance;
    candles.current = series;
    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const {
          market: data,
          markers: marks,
          plans: expectations,
        } = latest.current;
        const width = instance.timeScale().width();
        const height = 400;
        const coordinates = marks.flatMap((marker) => {
          const x = instance.timeScale().timeToCoordinate(marker.date as Time);
          if (x === null || x < 0 || x > width) return [];
          const y = series.priceToCoordinate(
            Number(marker.average),
          );
          if (y === null) return [];
          return [
            {
              marker,
              x: Number(x),
              y: Number(y),
            },
          ];
        });
        const lines = expectations.flatMap((plan) => {
          if (plan.status === "unreliable") return [];
          const timeScale = instance.timeScale();
          const start = timeScale.timeToCoordinate(plan.start_time as Time);
          const y = series.priceToCoordinate(Number(plan.reference_price));
          const range = timeScale.getVisibleRange();
          // 时间早于当前窗口的线从左边缘进入；绝不延伸到其真实起点之前。
          const x =
            start === null && range && plan.start_time < String(range.from)
              ? 0
              : start;
          const end = plan.end_time
            ? timeScale.timeToCoordinate(plan.end_time as Time)
            : width;
          if (x === null || end === null || y === null || x > width || end < 0)
            return [];
          return [
            {
              plan,
              x: Math.max(0, Number(x)),
              end: Math.min(width, Number(end)),
              y: Number(y),
            },
          ];
        });
        setLayout({ width, height, markers: coordinates, lines });
      });
    };
    redraw.current = update;
    instance.timeScale().subscribeVisibleLogicalRangeChange(update);
    instance.subscribeCrosshairMove((point) => {
      setCandle(latest.current.market.candles.find((item) => item.time === point.time) ?? null);
      update();
    });
    host.addEventListener("pointermove", update);
    host.addEventListener("pointerup", update);
    const observer = new ResizeObserver(update);
    observer.observe(host);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      host.removeEventListener("pointermove", update);
      host.removeEventListener("pointerup", update);
      instance.remove();
      chart.current = null;
    };
  }, []);
  useEffect(() => {
    const instance = chart.current!;
    const range = instance.timeScale().getVisibleLogicalRange();
    candles.current!.setData(
      market.candles.map((c) => ({
        time: c.time as Time,
        open: Number(c.open),
        high: Number(c.high),
        low: Number(c.low),
        close: Number(c.close),
      })),
    );
    if (range) instance.timeScale().setVisibleLogicalRange(range);
    else instance.timeScale().fitContent();
    redraw.current();
  }, [market]);
  useEffect(() => {
    chart.current!.applyOptions({
      layout: {
        background: {
          type: ColorType.Solid,
          color: dark ? "#171717" : "#ffffff",
        },
        textColor: dark ? "#d4d4d4" : "#525252",
      },
      grid: {
        vertLines: { color: dark ? "#262626" : "#f1f1f1" },
        horzLines: { color: dark ? "#262626" : "#f1f1f1" },
      },
    });
    redraw.current();
  }, [dark]);
  useEffect(() => {
    redraw.current();
  }, [plans, markers]);
  useEffect(() => {
    if (chosen && !plans.some((plan) => plan.id === chosen)) setChosen("");
  }, [plans, chosen]);
  return (
    <div className="chart-shell">
      <div ref={element} className="chart" aria-label="不复权日 K 图" />
      {candle && <div className="candle-prices" role="status" style={{ right: `calc(100% - ${layout.width}px + 12px)`, maxWidth: Math.max(0, layout.width - 24) }}>
        <time>{candle.time}</time>
        <dl>{([['开', candle.open], ['高', candle.high], ['低', candle.low], ['收', candle.close]] as const).map(([label, value]) => (
          <div key={label}><dt>{label}</dt><dd>{Number(value).toFixed(priceDigits)}</dd></div>
        ))}</dl>
      </div>}
      <svg
        className="chart-overlay"
        width={layout.width}
        height={layout.height}
        aria-label="成交标记与逐笔期待线"
      >
        <defs>
          <clipPath id="plot-clip">
            <rect width={layout.width} height={layout.height} />
          </clipPath>
        </defs>
        <g clipPath="url(#plot-clip)">
          {layout.lines.map(({ plan, x, end, y }) => (
            <g key={plan.id}>
              <line
                data-plan-id={plan.id}
                x1={x}
                x2={end}
                y1={y}
                y2={y}
                stroke={plan.side === "buy" ? "#ef8c23" : "#3b82f6"}
                strokeWidth={chosen === plan.id || selected.includes(plan.source_event_id) ? 3 : 1.5}
                strokeDasharray={
                  plan.strategy === "retained_buy_pairing" ? "7 4" : undefined
                }
                opacity={plan.status === "completed" ? 0.4 : 0.8}
              />
            </g>
          ))}
          {layout.markers.map(({ marker, x, y }) => (
            <g
              key={marker.id}
              data-marker={marker.direction}
              role="button"
              tabIndex={0}
              aria-label={`${marker.date} ${marker.direction} ${marker.count} 笔`}
              className="chart-marker"
              transform={`translate(${x},${y})`}
              onMouseEnter={() => setHover(marker)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(marker)}
              onClick={() => onSelect(marker.event_ids)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onSelect(marker.event_ids);
              }}
            >
              <circle r={3} fill={marker.direction === "B" ? "#ef8c23" : "#3b82f6"} />
              <line x1={0} y1={0} x2={0} y2={marker.direction === "B" ? 30 : -30} stroke={marker.direction === "B" ? "#ef8c23" : "#3b82f6"} strokeDasharray="3 2" />
              <rect x={-9} y={marker.direction === "B" ? 30 : -48} width={18} height={18} rx={4} fill={marker.direction === "B" ? "#ef8c23" : "#3b82f6"} />
              <text x={0} y={marker.direction === "B" ? 39 : -39} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={14} fontWeight={400} fontFamily="Inter, sans-serif">{marker.direction}</text>
            </g>
          ))}
        </g>
      </svg>
      {hover && (
        <div className="chart-tooltip" role="status">
          <span className={hover.direction === "B" ? "buy" : "sell"}>
            {hover.date} · {hover.direction === "B" ? "买入" : "卖出"}
          </span>
          <br />
          数量 {hover.quantity} · 均价 {Number(hover.average).toFixed(priceDigits)}
          <br />
          区间 {Number(hover.low).toFixed(priceDigits)}—{Number(hover.high).toFixed(priceDigits)} · {hover.count} 笔
          {hover.same_price && (
            <>
              <br />
              当日买卖均价相同
            </>
          )}
        </div>
      )}
      {!!plans.length && <div className="chart-source"><label>图中价位 <select aria-label="期待来源" value={chosen} onChange={(e) => setChosen(e.target.value)}>
          <option value="">选择价位</option>
          {[...plans].sort((a, b) => {
            const time = b.start_time.localeCompare(a.start_time);
            if (time) return time;
            const first = events.find((event) => event.id === a.source_event_id);
            const second = events.find((event) => event.id === b.source_event_id);
            return (second?.time ?? "").localeCompare(first?.time ?? "")
              || (second?.line ?? 0) - (first?.line ?? 0);
          }).map((plan) => {
            return <option key={plan.id} value={plan.id}>{plan.account} · {plan.start_time} · {plan.side === "sell" ? "卖出" : "买回"} {plan.comparison}{Number(plan.reference_price).toFixed(priceDigits)} · {plan.remaining_quantity} 股</option>;
          })}
        </select></label><Button variant="outline" disabled={!chosen} onClick={() => { const plan = plans.find((item) => item.id === chosen); if (plan) onSelect([plan.source_event_id]); }}>查看成交</Button></div>}
      <p className="muted text-xs">
        拖动时间轴或价格轴缩放。
      </p>
    </div>
  );
}
