import { useLayoutEffect, useRef, useState, type KeyboardEvent, type PointerEvent, type ReactNode } from "react";
import { Table2, LineChart as LineIcon } from "lucide-react";
import { Button } from "@/components/ui/button";

// Plain SVG/HTML charts: no chart dependency. Colors are role tokens in index.css
// (validated categorical slots; light slots 3-5 need the legend and table view).
export const number = new Intl.NumberFormat("es", { maximumFractionDigits: 1 });
export const percent = (value: number | null | undefined) => value == null ? "—" : `${number.format(value)} %`;
const shortDay = new Intl.DateTimeFormat("es", { day: "numeric", month: "short", timeZone: "UTC" });
const longDay = new Intl.DateTimeFormat("es", { weekday: "short", day: "numeric", month: "long", timeZone: "UTC" });

type Tip = { x: number; y: number; content: ReactNode } | null;

function useTooltip() {
  const frame = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<Tip>(null);
  function show(target: Element, content: ReactNode) {
    const box = frame.current?.getBoundingClientRect();
    const rect = target.getBoundingClientRect();
    if (box) setTip({ x: rect.left - box.left + rect.width / 2, y: rect.top - box.top, content });
  }
  const node = tip && <div className="chart-tooltip" role="presentation" style={{ left: tip.x, top: tip.y }}>{tip.content}</div>;
  return { frame, show, hide: () => setTip(null), node };
}

function useWidth() {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry?.contentRect.width ?? 0)));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** Card with title, definition and the table-view twin every chart needs. */
export function ChartFrame({ title, definition, table, legend, children, busy = false, id }: { title: string; definition?: string; table: ReactNode; legend?: ReactNode; children: ReactNode; busy?: boolean; id: string }) {
  const [tableView, setTableView] = useState(false);
  return <section className="surface chart-frame" aria-labelledby={`${id}-title`} data-busy={busy || undefined}>
    <header className="chart-heading">
      <div><h2 id={`${id}-title`}>{title}</h2>{definition && <p>{definition}</p>}</div>
      <Button variant="ghost" size="sm" aria-pressed={tableView} onClick={() => setTableView(!tableView)}>{tableView ? <LineIcon size={14} /> : <Table2 size={14} />}{tableView ? "Ver gráfico" : "Ver tabla"}</Button>
    </header>
    {!tableView && legend}
    {tableView ? <div className="table-scroll">{table}</div> : children}
  </section>;
}

export function Legend({ items }: { items: { key: string; label: string; color: string; line?: boolean }[] }) {
  return <ul className="chart-legend">{items.map(item => <li key={item.key}><span aria-hidden="true" className={item.line ? "legend-line" : "legend-swatch"} style={{ background: item.color }} />{item.label}</li>)}</ul>;
}

export type BarRow = { key: string; label: string; value: number; note?: string };

/** Single-series horizontal bars with the value at each tip. */
export function BarList({ rows, empty }: { rows: BarRow[]; empty: string }) {
  const { frame, show, hide, node } = useTooltip();
  const max = Math.max(1, ...rows.map(row => row.value));
  if (!rows.length) return <p className="chart-empty">{empty}</p>;
  return <div className="chart-plot" ref={frame} onPointerLeave={hide}>
    <ul className="bar-list">{rows.map(row => {
      const content = <><strong>{number.format(row.value)}</strong><span>{row.label}{row.note ? ` · ${row.note}` : ""}</span></>;
      return <li key={row.key} tabIndex={0} aria-label={`${row.label}: ${number.format(row.value)}${row.note ? `, ${row.note}` : ""}`}
        onPointerMove={e => show(e.currentTarget.querySelector(".bar-fill") ?? e.currentTarget, content)} onFocus={e => show(e.currentTarget.querySelector(".bar-fill") ?? e.currentTarget, content)} onBlur={hide}>
        <span className="bar-label">{row.label}</span>
        <span className="bar-track"><span className="bar-fill" style={{ width: `${(row.value / max) * 100}%` }} /></span>
        <span className="bar-value">{number.format(row.value)}{row.note && <small>{row.note}</small>}</span>
      </li>;
    })}</ul>
    {node}
  </div>;
}

export type StackRow = { key: string; label: string; total: number; segments: { key: string; label: string; value: number; color: string }[] };

/** Part-to-whole per row; segments are separated by a 2px surface gap. */
export function StackedBars({ rows, unit, empty }: { rows: StackRow[]; unit: string; empty: string }) {
  const { frame, show, hide, node } = useTooltip();
  if (!rows.some(row => row.total)) return <p className="chart-empty">{empty}</p>;
  return <div className="chart-plot" ref={frame} onPointerLeave={hide}>
    {rows.map(row => <div className="stack-row" key={row.key}>
      <span className="stack-label">{row.label}<small>{number.format(row.total)} {unit}</small></span>
      <div className="stack-track">{row.total ? row.segments.filter(s => s.value).map(segment => {
        const share = segment.value * 100 / row.total;
        const content = <><strong>{number.format(segment.value)} · {percent(share)}</strong><span>{segment.label} · {row.label}</span></>;
        return <span key={segment.key} className="stack-segment" tabIndex={0} style={{ flexGrow: segment.value, background: segment.color }}
          aria-label={`${row.label}, ${segment.label}: ${number.format(segment.value)} (${percent(share)})`}
          onPointerMove={e => show(e.currentTarget, content)} onFocus={e => show(e.currentTarget, content)} onBlur={hide} />;
      }) : <span className="stack-none">Sin mensajes</span>}</div>
    </div>)}
    {node}
  </div>;
}

function niceScale(max: number) {
  const raw = Math.max(1, max) / 4;
  const power = 10 ** Math.floor(Math.log10(raw));
  const step = Math.max(1, [1, 2, 5, 10].map(m => m * power).find(s => s >= raw) ?? power * 10);
  const top = Math.ceil(Math.max(1, max) / step) * step;
  return { top, ticks: Array.from({ length: top / step + 1 }, (_, i) => i * step) };
}

export type LineSeries = { key: string; label: string; color: string; values: number[] };

/** Multi-series daily line chart with a snapping crosshair (pointer and arrow keys). */
export function LineChart({ days, series, label }: { days: string[]; series: LineSeries[]; label: string }) {
  const [ref, width] = useWidth();
  const [active, setActive] = useState<number | null>(null);
  const height = 236, pad = { top: 14, right: 18, bottom: 30, left: 38 };
  const plotW = Math.max(0, width - pad.left - pad.right), plotH = height - pad.top - pad.bottom;
  const { top, ticks } = niceScale(Math.max(0, ...series.flatMap(s => s.values)));
  const x = (i: number) => pad.left + (days.length <= 1 ? plotW / 2 : (i * plotW) / (days.length - 1));
  const y = (v: number) => pad.top + plotH - (v / top) * plotH;
  const labelled = days.length <= 2 ? days.map((_, i) => i) : [0, Math.floor((days.length - 1) / 2), days.length - 1];
  function pick(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const ratio = days.length <= 1 ? 0 : (event.clientX - box.left - pad.left) / plotW;
    setActive(Math.min(days.length - 1, Math.max(0, Math.round(ratio * (days.length - 1)))));
  }
  function key(event: KeyboardEvent<SVGSVGElement>) {
    const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, Home: -days.length, End: days.length };
    if (!(event.key in moves)) return;
    event.preventDefault();
    setActive(Math.min(days.length - 1, Math.max(0, (active ?? days.length - 1) + moves[event.key]!)));
  }
  const day = active == null ? null : days[active];
  return <div className="chart-plot line-chart" ref={ref}>
    {width > 0 && <svg width={width} height={height} role="img" tabIndex={0} aria-label={`${label}. Usa las flechas para recorrer los días.`}
      onPointerMove={pick} onPointerLeave={() => setActive(null)} onKeyDown={key} onFocus={() => setActive(days.length - 1)} onBlur={() => setActive(null)}>
      {ticks.map(t => <g key={t}><line className="chart-grid" x1={pad.left} x2={width - pad.right} y1={y(t)} y2={y(t)} /><text className="chart-axis" x={pad.left - 8} y={y(t)} dy="0.32em" textAnchor="end">{number.format(t)}</text></g>)}
      <line className="chart-baseline" x1={pad.left} x2={width - pad.right} y1={y(0)} y2={y(0)} />
      {labelled.map(i => <text key={i} className="chart-axis" x={x(i)} y={height - 8} textAnchor={days.length > 1 && i === 0 ? "start" : days.length > 1 && i === days.length - 1 ? "end" : "middle"}>{shortDay.format(new Date(days[i]!))}</text>)}
      {active != null && <line className="chart-crosshair" x1={x(active)} x2={x(active)} y1={pad.top} y2={pad.top + plotH} />}
      {series.map(s => <g key={s.key}>
        {days.length > 1 && <polyline className="chart-line" fill="none" stroke={s.color} points={s.values.map((v, i) => `${x(i)},${y(v)}`).join(" ")} />}
        {(active != null || days.length === 1) && <circle className="chart-dot" cx={x(active ?? 0)} cy={y(s.values[active ?? 0] ?? 0)} r={4} fill={s.color} />}
      </g>)}
    </svg>}
    {day != null && active != null && <div className={`chart-tooltip ${active > (days.length - 1) / 2 ? "tip-left" : "tip-right"}`} role="status" style={{ left: x(active), top: pad.top }}>
      <span className="tooltip-title">{longDay.format(new Date(day))}</span>
      {series.map(s => <span key={s.key} className="tooltip-row"><span aria-hidden="true" className="legend-line" style={{ background: s.color }} /><strong>{number.format(s.values[active] ?? 0)}</strong>{s.label}</span>)}
    </div>}
  </div>;
}
