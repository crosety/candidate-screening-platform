import { useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { ArrowUpRight, CircleHelp, Clock, LockKeyhole, MessageSquare, Sparkles, Timer, UserMinus, UserRound } from "lucide-react";
import { api, type components } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { BarList, ChartFrame, Legend, LineChart, StackedBars, number, percent } from "@/components/AnalyticsCharts";
import { RecruiterHistory } from "@/components/RecruiterHistory";
import { MetricCard, PageHeader } from "@/components/WorkspaceUI";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { channelLabel, languageLabel, moodLabel, moods } from "@/lib/observations";

type Metrics = components["schemas"]["MetricsOut"];
type Breakdown = components["schemas"]["BreakdownOut"];
type Filters = { period: string; since: string; until: string; offer: string; channel: string; language: string; environment: string };

const seriesColors = { started: "var(--viz-1)", completed: "var(--viz-2)", abandoned: "var(--viz-3)" };
const moodColors: Record<string, string> = { neutral: "var(--viz-1)", positive: "var(--viz-2)", confused: "var(--viz-3)", anxious: "var(--viz-4)", frustrated: "var(--viz-5)", unknown: "var(--viz-other)" };
// Ordered pairs: numeric object keys would otherwise be listed before "all".
const periods: [string, string][] = [["all", "Todo el historial"], ["7", "Últimos 7 días"], ["30", "Últimos 30 días"], ["90", "Últimos 90 días"], ["custom", "Personalizado"]];

function seconds(value: number | null | undefined) {
  if (value == null) return "—";
  if (value < 60) return `${number.format(value)} s`;
  const minutes = Math.round(value / 60);
  return minutes < 60 ? `${minutes} min` : `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
}

function range(filters: Filters) {
  if (filters.period === "custom") return { since: filters.since || undefined, until: filters.until || undefined };
  if (filters.period === "all") return { since: undefined, until: undefined };
  const since = new Date(Date.now() - (Number(filters.period) - 1) * 86_400_000);
  return { since: since.toISOString().slice(0, 10), until: undefined };
}

export function AnalyticsPage() {
  const { tenantId, tenant } = useTenant();
  const fde = tenant.role !== "recruiter";
  const [filters, setFilters] = useState<Filters>({ period: "all", since: "", until: "", offer: "", channel: "", language: "", environment: "production" });
  const [languages, setLanguages] = useState<string[]>([]);
  const query = { offer: filters.offer, channel: filters.channel, language: filters.language, environment: fde ? filters.environment : "production", ...range(filters) };
  const read = async (group: "metrics" | "agreement") => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/analytics", { params: { path: { tenant_id: tenantId }, query: { group, ...query } } });
    if (!r.data) throw new Error("No se puede cargar la analítica.");
    return r.data;
  };
  const metrics = useQuery({ queryKey: ["analytics-metrics", tenantId, query], placeholderData: keepPreviousData, queryFn: async () => {
    const data = await read("metrics");
    if (data.group !== "metrics") throw new Error("Respuesta de analítica no válida.");
    return data;
  }});
  const agreement = useQuery({ queryKey: ["analytics-agreement", tenantId, query], placeholderData: keepPreviousData, queryFn: async () => {
    const data = await read("agreement");
    if (data.group !== "agreement") throw new Error("Respuesta de acuerdo no válida.");
    return data;
  }});
  const offers = useQuery({ queryKey: ["offers", tenantId], queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/offers", { params: { path: { tenant_id: tenantId } } });
    if (!r.data) throw new Error("No se pueden cargar las ofertas.");
    return r.data;
  }});
  const data = metrics.data;
  // Language options accumulate what has been observed, so a selected filter keeps its option.
  useEffect(() => {
    const seen = data?.by_language?.map(row => row.key) ?? [];
    if (seen.length) setLanguages(current => [...new Set([...current, ...seen])].sort());
  }, [data]);
  const busy = metrics.isFetching && metrics.isPlaceholderData;
  const definition = (key: string) => data?.definitions?.find(item => item.key === key)?.text;
  const set = (patch: Partial<Filters>) => setFilters({ ...filters, ...patch });
  const closedNote = (n: number | undefined) => data ? `${number.format(n ?? 0)} de ${number.format(data.closed ?? 0)} cerradas` : "";

  return <div className="analytics-page space-y-6">
    <PageHeader eyebrow="Vista de selección" title="Analítica" description="Cómo avanzan las conversaciones: finalización, abandono, tiempos y etapas. Cada cifra indica qué cuenta y sobre qué total." actions={<Button variant="outline" asChild><Link to={`/t/${tenantId}/candidaturas`}>Ver candidaturas<ArrowUpRight size={14} /></Link></Button>} />

    <div className="analytics-filters" role="group" aria-label="Filtros de analítica">
      <label>Periodo<select aria-label="Periodo" value={filters.period} onChange={e => set({ period: e.target.value })}>{periods.map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      {filters.period === "custom" && <><label>Desde<input type="date" aria-label="Desde" value={filters.since} onChange={e => set({ since: e.target.value })} /></label><label>Hasta<input type="date" aria-label="Hasta" value={filters.until} onChange={e => set({ until: e.target.value })} /></label></>}
      <label>Oferta<select aria-label="Oferta" value={filters.offer} onChange={e => set({ offer: e.target.value })}><option value="">Todas</option>{offers.data?.offers.map(offer => <option key={offer.key} value={offer.key}>{offer.title}</option>)}</select></label>
      <label>Canal<select aria-label="Canal" value={filters.channel} onChange={e => set({ channel: e.target.value })}><option value="">Todos</option>{["web", "whatsapp", "voice", "telephone"].map(channel => <option key={channel} value={channel}>{channelLabel(channel)}</option>)}</select></label>
      <label>Idioma<select aria-label="Idioma" value={filters.language} onChange={e => set({ language: e.target.value })}><option value="">Todos</option>{languages.map(code => <option key={code} value={code}>{languageLabel(code)}</option>)}</select></label>
      {fde && <label>Datos<select aria-label="Datos" value={filters.environment} onChange={e => set({ environment: e.target.value })}><option value="production">Operación</option><option value="simulation">Simulaciones</option></select></label>}
    </div>
    {offers.isError && <div role="alert" className="inline-error">No se pueden cargar las ofertas. <Button variant="outline" onClick={() => void offers.refetch()}>Reintentar ofertas</Button></div>}
    {metrics.isError && <div role="alert" className="surface flex flex-wrap items-center gap-3 p-4"><span>No se puede cargar la analítica.</span><Button variant="outline" onClick={() => void metrics.refetch()}>Reintentar analítica</Button></div>}
    {metrics.isPending && <p role="status">Cargando analítica…</p>}
    {busy && <p role="status">Actualizando los filtros; se muestran los datos anteriores…</p>}

    {data && !metrics.isError && <>
    <section className="metrics-grid" aria-label="Resultados de las conversaciones" data-busy={busy || undefined}>
      <MetricCard label="Conversaciones" value={data?.total} description={data ? `${number.format(data.in_progress ?? 0)} en curso · ${number.format(data.closed ?? 0)} cerradas` : "Cargando"} icon={MessageSquare} />
      <MetricCard label="Finalización" kind="completed" value={data && percent(data.completion_pct)} description={closedNote(data?.completed)} />
      <MetricCard label="Abandono" kind="abandoned" icon={UserMinus} value={data && percent(data.abandonment_pct)} description={closedNote(data?.abandoned)} />
      <MetricCard label="Derivación a una persona" kind="handed_off" value={data && percent(data.handoff_pct)} description={closedNote(data?.handed_off)} icon={UserRound} />
    </section>
    {data && <p className="analytics-note">{definition("completion")}</p>}

    <section className="analytics-times" aria-label="Tiempos" data-busy={busy || undefined}>
      <div className="surface"><p className="metric-label">Duración de las completadas<Timer size={16} aria-hidden="true" /></p><p className="analytics-pair"><span><strong>{seconds(data?.median_duration_seconds)}</strong>mediana</span><span><strong>{seconds(data?.p90_duration_seconds)}</strong>percentil 90</span></p><p>{number.format(data?.duration_samples ?? 0)} conversaciones completadas. {definition("duration")}</p></div>
      <div className="surface"><p className="metric-label">Primera respuesta<Clock size={16} aria-hidden="true" /></p><p className="analytics-pair"><span><strong>{seconds(data?.median_first_response_seconds)}</strong>mediana</span><span><strong>{seconds(data?.p90_first_response_seconds)}</strong>percentil 90</span></p><p>{number.format(data?.first_response_samples ?? 0)} conversaciones con respuesta. {definition("first_response")}</p></div>
      <div className="surface"><p className="metric-label">Respuesta tras un recordatorio<ArrowUpRight size={16} aria-hidden="true" /></p><p className="analytics-pair"><span><strong>{percent(data?.response_pct)}</strong>{number.format(data?.responded_after_followup ?? 0)} de {number.format(data?.followup_recipients ?? 0)} contactadas</span></p><p>{data?.note}</p></div>
    </section>

    <div className="analytics-grid">
      <ChartFrame id="funnel" title="Embudo de conversación" definition={definition("funnel")} busy={busy}
        table={<table className="candidate-table"><thead><tr><th>Paso</th><th>Conversaciones</th><th>Sobre iniciadas</th></tr></thead><tbody>{data?.funnel?.map(step => <tr key={step.key}><td>{step.label}</td><td>{number.format(step.count)}</td><td>{percent(step.pct)}</td></tr>)}</tbody></table>}>
        <BarList empty="Todavía no hay conversaciones en este filtro." rows={data?.total ? (data.funnel ?? []).map(step => ({ key: step.key, label: step.label, value: step.count, note: `${percent(step.pct)} de iniciadas` })) : []} />
      </ChartFrame>
      <ChartFrame id="stages" title="Etapa en la que se abandona" definition={definition("stages")} busy={busy}
        table={<table className="candidate-table"><thead><tr><th>Dato pendiente</th><th>Abandonos</th><th>Sobre abandonos</th><th>Último ánimo observado</th></tr></thead><tbody>{data?.stages?.map(stage => <tr key={stage.key}><td>{stage.label}</td><td>{number.format(stage.count)}</td><td>{percent(stage.pct)}</td><td>{stage.moods?.map(m => `${moodLabel(m.label)} ${m.count}`).join(" · ") || "—"}</td></tr>)}</tbody></table>}>
        <BarList empty="No hay abandonos en este filtro." rows={(data?.stages ?? []).map(stage => ({ key: stage.key || "none", label: stage.label, value: stage.count, note: `${percent(stage.pct)} de abandonos` }))} />
      </ChartFrame>
    </div>

    <ChartFrame id="series" title="Actividad diaria" definition={definition("series")} busy={busy}
      legend={<Legend items={[{ key: "started", label: "Iniciadas", color: seriesColors.started, line: true }, { key: "completed", label: "Completadas", color: seriesColors.completed, line: true }, { key: "abandoned", label: "Abandonadas", color: seriesColors.abandoned, line: true }]} />}
      table={<table className="candidate-table"><thead><tr><th>Día</th><th>Iniciadas</th><th>Completadas</th><th>Abandonadas</th></tr></thead><tbody>{data?.series?.map(day => <tr key={day.day}><td>{new Date(day.day).toLocaleDateString("es", { timeZone: "UTC" })}</td><td>{day.started}</td><td>{day.completed}</td><td>{day.abandoned}</td></tr>)}</tbody></table>}>
      {data?.series?.length ? <LineChart label="Conversaciones iniciadas, completadas y abandonadas por día" days={data.series.map(day => day.day)} series={(["started", "completed", "abandoned"] as const).map(key => ({ key, label: { started: "Iniciadas", completed: "Completadas", abandoned: "Abandonadas" }[key], color: seriesColors[key], values: data.series!.map(day => day[key]) }))} /> : <p className="chart-empty">Sin actividad en este filtro.</p>}
    </ChartFrame>

    <section className="surface chart-frame" aria-labelledby="breakdown-title" data-busy={busy || undefined}>
      <header className="chart-heading"><div><h2 id="breakdown-title">Desglose por canal, oferta e idioma</h2><p>Mismas definiciones que arriba: tasas sobre conversaciones cerradas de cada grupo.</p></div></header>
      <Tabs defaultValue="channel">
        <TabsList><TabsTrigger value="channel">Canal</TabsTrigger><TabsTrigger value="offer">Oferta</TabsTrigger><TabsTrigger value="language">Idioma</TabsTrigger></TabsList>
        <TabsContent value="channel"><BreakdownTable rows={data?.by_channel ?? []} label={row => channelLabel(row.key)} note={definition("channel")} /></TabsContent>
        <TabsContent value="offer"><BreakdownTable rows={data?.by_offer ?? []} label={row => row.label} note={definition("offer")} /></TabsContent>
        <TabsContent value="language"><BreakdownTable rows={data?.by_language ?? []} label={row => languageLabel(row.key)} note={definition("language")} /></TabsContent>
      </Tabs>
    </section>

    <MoodSection data={data} busy={busy} tenantId={tenantId} definition={definition("mood")} />

    <section className="surface chart-frame" aria-labelledby="agreement-title">
      <header className="chart-heading"><div><h2 id="agreement-title">Acuerdo con la revisión humana</h2><p>{agreement.data?.note}</p></div></header>
      {agreement.isError ? <div role="alert" className="inline-error">No se ha podido cargar el acuerdo. <Button variant="outline" onClick={() => void agreement.refetch()}>Reintentar acuerdo</Button></div>
        : agreement.isPending ? <p role="status">Cargando acuerdo…</p>
        : agreement.data?.items.length ? <div className="table-scroll"><table className="candidate-table"><thead><tr><th>Versión</th><th>Revisadas</th><th>Acuerdo</th></tr></thead><tbody>{agreement.data.items.map(row => <tr key={row.version}><td>v{row.version}</td><td>{row.agree} de {row.reviewed}</td><td>{percent(row.agreement_pct)}</td></tr>)}</tbody></table></div> : <p className="chart-empty">Todavía no hay decisiones revisadas para calcular el acuerdo.</p>}
    </section>

    <div className="analytics-assistant"><Assistant tenantId={tenantId} /><RecruiterHistory tenantId={tenantId} /></div>

    <details className="surface disclosure-panel"><summary><CircleHelp size={15} /><span>Cómo se calcula cada cifra</span></summary><dl className="disclosure-content analytics-definitions">{data?.definitions?.map(item => <div key={item.key}><dt>{({ population: "Conversaciones", completion: "Finalización", abandonment: "Abandono", handoff: "Derivación", duration: "Duración", first_response: "Primera respuesta", funnel: "Embudo", stages: "Etapa de abandono", channel: "Canal", offer: "Oferta", language: "Idioma", series: "Actividad diaria", mood: "Sentimiento" } as Record<string, string>)[item.key] ?? item.key}</dt><dd>{item.text}</dd></div>)}</dl></details>
    </>}
  </div>;
}

function BreakdownTable({ rows, label, note }: { rows: Breakdown[]; label: (row: Breakdown) => string; note?: string }) {
  const rate = (value: number | null | undefined, part: number | undefined, row: Breakdown) => <>{percent(value)}<small>{number.format(part ?? 0)}/{number.format(row.closed ?? 0)}</small></>;
  return <>{note && <p className="analytics-note">{note}</p>}{rows.length ? <div className="table-scroll"><table className="candidate-table analytics-table"><thead><tr><th>Grupo</th><th>Iniciadas</th><th>En curso</th><th>Finalización</th><th>Abandono</th><th>Derivación</th><th>Duración mediana</th></tr></thead><tbody>{rows.map(row => <tr key={row.key}><td>{label(row)}</td><td>{number.format(row.total)}</td><td>{number.format(row.in_progress ?? 0)}</td><td>{rate(row.completion_pct, row.completed, row)}</td><td>{rate(row.abandonment_pct, row.abandoned, row)}</td><td>{rate(row.handoff_pct, row.handed_off, row)}</td><td>{seconds(row.median_duration_seconds)}</td></tr>)}</tbody></table></div> : <p className="chart-empty">Sin conversaciones en este filtro.</p>}</>;
}

function MoodSection({ data, busy, tenantId, definition }: { data: Metrics | undefined; busy: boolean; tenantId: string; definition?: string }) {
  const phases = data?.moods ?? [];
  const quotes = phases.flatMap(phase => phase.items.flatMap(item => (item.quotes ?? []).map(quote => ({ ...quote, mood: item.mood, phase: phase.label }))));
  return <ChartFrame id="mood" title="Observación de sentimiento (no es criterio de selección)" definition={definition} busy={busy}
    legend={<Legend items={moods.map(mood => ({ key: mood, label: moodLabel(mood), color: moodColors[mood]! }))} />}
    table={<table className="candidate-table"><thead><tr><th>Fase</th>{moods.map(mood => <th key={mood}>{moodLabel(mood)}</th>)}<th>Mensajes</th></tr></thead><tbody>{phases.map(phase => <tr key={phase.key}><td>{phase.label}</td>{moods.map(mood => <td key={mood}>{phase.items.find(item => item.mood === mood)?.count ?? 0}</td>)}<td>{phase.total}</td></tr>)}</tbody></table>}>
    <StackedBars unit="mensajes" empty="Sin mensajes del candidato en este filtro." rows={phases.map(phase => ({ key: phase.key, label: phase.label, total: phase.total, segments: moods.map(mood => ({ key: mood, label: moodLabel(mood), value: phase.items.find(item => item.mood === mood)?.count ?? 0, color: moodColors[mood]! })) }))} />
    {data?.quotes_visible ? quotes.length > 0 && <details className="mood-quotes"><summary>Frases literales observadas ({quotes.length})</summary><p>Fragmentos exactos de conversaciones que puedes abrir. Sirven para revisar el tono del asistente, no para valorar a la persona.</p><ul>{quotes.map(quote => <li key={quote.message_id}><blockquote>«{quote.quote}»</blockquote><span>{moodLabel(quote.mood)} · {quote.phase} · <Link to={`/t/${tenantId}/candidaturas/${quote.session_id}`}>Abrir conversación</Link></span></li>)}</ul></details>
      : <p className="chart-empty">Las frases literales solo se muestran a quien puede abrir esas conversaciones.</p>}
  </ChartFrame>;
}

function Assistant({ tenantId }: { tenantId: string }) {
  const [question, setQuestion] = useState("");
  const [reply, setReply] = useState("");
  const [busy, setBusy] = useState(false);
  async function ask() {
    setBusy(true);
    try {
      const r = await api.POST("/api/v1/tenants/{tenant_id}/recruiter/ask", { params: { path: { tenant_id: tenantId } }, body: { question } });
      setReply(r.data?.reply ?? "No se ha podido responder.");
    } catch { setReply("No se ha podido responder."); } finally { setBusy(false); }
  }
  return <section className="surface assistant-panel" aria-labelledby="assistant-title">
    <div className="assistant-heading"><span className="assistant-icon"><Sparkles size={19} strokeWidth={1.6} /></span><span className="readonly-label"><LockKeyhole size={11} /> Solo lectura</span></div>
    <h2 id="assistant-title">Pregunta sobre tus datos</h2>
    <p className="assistant-description">Consulta recuentos y métricas en lenguaje natural, sin cambiar datos ni tomar decisiones. No recibe frases de los candidatos.</p>
    <div className="assistant-suggestions">{["¿Cuál es la tasa de abandono por oferta?", "¿Cuántas candidaturas hay por estado?"].map(suggestion => <button key={suggestion} onClick={() => { setQuestion(suggestion); document.getElementById("analytics-question")?.focus(); }}><MessageSquare size={13} /><span>{suggestion}</span><ArrowUpRight size={13} /></button>)}</div>
    <form onSubmit={e => { e.preventDefault(); void ask(); }}><Textarea id="analytics-question" aria-label="Pregunta al asistente" value={question} onChange={e => setQuestion(e.target.value)} maxLength={2000} placeholder="¿Qué te gustaría saber?" rows={3} /><Button type="submit" className="w-full mt-3" disabled={busy || !question.trim()}>{busy ? "Consultando…" : "Consultar"}<ArrowUpRight size={14} /></Button></form>
    {reply && <div className="assistant-reply" aria-live="polite"><p className="eyebrow">Respuesta</p><p>{reply}</p></div>}
    <div className="assistant-footnote"><CircleHelp size={13} /><span>Las respuestas respetan tus permisos y segmentos.</span></div>
  </section>;
}
