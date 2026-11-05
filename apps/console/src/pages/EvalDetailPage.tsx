import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api } from "@screening/api-client";
import { ChartNoAxesCombined, Coins, FlaskConical, Gauge } from "lucide-react";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { cost } from "@/lib/sessions";
import { MetricCard, PageHeader, StatusBadge } from "@/components/WorkspaceUI";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { evalStatuses, verdicts } from "./EvalsPage";

type Judge = { error?: string; comment?: string; items?: { key: string; score: number; comment: string }[]; checks?: { text: string; passed: boolean; evidence?: string }[] };

export function EvalDetailPage() {
  const { tenantId } = useTenant();
  const { runId = "" } = useParams();
  const [expanded, setExpanded] = useState<string | null>(null);
  const detail = useQuery({ queryKey: ["eval", tenantId, runId], refetchInterval: q => ["queued", "running"].includes(q.state.data?.status ?? "") ? 3000 : false, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/evals/{run_id}", { params: { path: { tenant_id: tenantId, run_id: runId } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  // Each offer has its own personas; the run records which suite it evaluated.
  const offerKey = String(detail.data?.summary.offer_key ?? "");
  const personas = useQuery({ queryKey: ["personas", tenantId, "languages", offerKey], enabled: !!detail.data, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/personas", { params: { path: { tenant_id: tenantId }, query: { languages: true, offer_key: offerKey } } }); return result.data;
  }});
  if (detail.error) return <p role="alert">{detail.error.message}</p>;
  if (!detail.data) return <p>Cargando ejecución…</p>;
  const run = detail.data;
  const expected = (run.summary.expected_verdicts ?? {}) as Record<string, string>;
  return <div className="space-y-6 fde-page"><Link className="text-sm underline" to={`/t/${tenantId}/evals`}>Volver a evals</Link><PageHeader eyebrow="Detalle de evaluación" title={`Ejecución · borrador rev ${run.draft_rev}`} description="Contrasta los resultados de cada escenario y abre su conversación para revisar la evidencia." actions={<StatusBadge status={run.status} />} />
    <div className="metrics-grid">
      <MetricCard label="Puntuación del borrador" value={run.score_draft?.toFixed(1) ?? "—"} description="Sobre 100 puntos" icon={Gauge} />
      <MetricCard label="Puntuación publicada" value={run.score_published?.toFixed(1) ?? "—"} description="Versión de comparación" kind="neutral" icon={ChartNoAxesCombined} />
      <MetricCard label="Casos de la ejecución" value={run.cases.length} description="Escenarios y objetivos" kind="neutral" icon={FlaskConical} />
      <MetricCard label="Coste de la ejecución" value={cost(run.cost_usd_micros)} description="Uso acumulado registrado" kind="neutral" icon={Coins} />
    </div><details className="surface p-4 text-sm"><summary>Arnés y criterios usados en esta ejecución</summary><pre className="overflow-auto text-xs mt-3">{JSON.stringify(run.summary, null, 2)}</pre></details><Card><CardContent><Table><TableHeader><TableRow>{["Persona", "Objetivo", "Esperado / obtenido", "Puntuación", "Coste", "Estado"].map(label => <TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader><TableBody>{run.cases.map(item => {
    const persona = personas.data?.find(p => p.key === item.persona_key);
    const judge = item.judge as Judge;
    const targetVerdict = expected[item.persona_key];
    return <Fragment key={item.id}><TableRow className="cursor-pointer" onClick={() => setExpanded(expanded === item.id ? null : item.id)}><TableCell><button aria-expanded={expanded === item.id}>{persona?.name ?? item.persona_key} · Ver caso</button></TableCell><TableCell>{item.target === "draft" ? "Borrador" : "Publicada"}</TableCell><TableCell className={targetVerdict !== item.verdict && item.status !== "pending" ? "text-red-700" : ""}>{verdicts[targetVerdict ?? ""] ?? "—"} / {verdicts[item.verdict] ?? "—"}</TableCell><TableCell>{item.score?.toFixed(1) ?? "—"}</TableCell><TableCell>{cost(item.cost_usd_micros)}</TableCell><TableCell>{evalStatuses[item.status]}</TableCell></TableRow>{expanded === item.id && <TableRow><TableCell colSpan={6} className="space-y-3 whitespace-normal"><h3 className="font-semibold">Transcripción</h3>{item.transcript.map((message, index) => <p key={index} className="text-sm"><strong>{message.from === "agent" ? (message.channel === "reminder" ? "Agente (recordatorio por silencio)" : "Agente") : "Candidato"}:</strong> {message.text}</p>)}<h3 className="font-semibold">Notas del juez</h3>{judge.error && <p className="text-red-700">{judge.error}</p>}<p>{judge.comment}</p>{judge.items?.map(note => <p key={note.key} className="text-sm">{note.key}: {note.score} · {note.comment}</p>)}{judge.checks?.map(check => <p key={check.text} className={`text-sm ${check.passed ? "text-emerald-700" : "text-red-700"}`}>{check.passed ? "✓" : "✗"} {check.text} · {check.evidence ?? "Sin cita"}</p>)}</TableCell></TableRow>}</Fragment>;
  })}</TableBody></Table></CardContent></Card></div>;
}
