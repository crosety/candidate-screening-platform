import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { cost } from "@/lib/sessions";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChevronDown, FlaskConical, Play, ShieldCheck, Users } from "lucide-react";
import { EmptyState, PageHeader, StatusBadge } from "@/components/WorkspaceUI";
import { useOffers } from "./OffersPage";

export const evalStatuses: Record<string, string> = { queued: "En cola", running: "En curso", done: "Hecha", failed: "Fallida", pending: "Pendiente", error: "Error" };
export const verdicts: Record<string, string> = { qualified: "Apto", not_qualified: "No apto", review: "Revisar", pool: "Bolsa" };

export function EvalsPage({ languages = false }: { languages?: boolean }) {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [selected, setSelected] = useState<string[] | null>(null);
  const [compare, setCompare] = useState(true);
  const [budget, setBudget] = useState(languages ? "0.3" : "3");
  const [chosenOffer, setOfferKey] = useState<string | null>(null);
  const offers = useOffers();
  // Open offers each need their own suite, so start on the first one instead of the common one.
  const offerKey = chosenOffer ?? offers.data?.offers.find(o => o.draft?.status === "open")?.key ?? "";
  const personas = useQuery({ queryKey: ["personas", tenantId, languages, offerKey], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/personas", { params: { path: { tenant_id: tenantId }, query: { languages, offer_key: offerKey } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  const keys = selected ?? personas.data?.map(p => p.key) ?? [];
  const runs = useQuery({ queryKey: ["evals", tenantId], refetchInterval: q => q.state.data?.some(r => ["queued", "running"].includes(r.status)) ? 3000 : false, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/evals", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  const gate = useQuery({ queryKey: ["gate", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/draft/gate", { params: { path: { tenant_id: tenantId } } }); return result.data;
  }});
  const start = useMutation({ mutationFn: async () => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/evals", { params: { path: { tenant_id: tenantId } }, body: { persona_keys: keys, compare_published: compare, max_cost_usd: Number(budget), offer_key: offerKey } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }, onSuccess: () => void cache.invalidateQueries({ queryKey: ["evals", tenantId] }) });
  return <div className="space-y-6"><PageHeader eyebrow="Calidad del agente" title={languages ? "Evals de idiomas" : "Evals"} description="Comprueba el comportamiento del agente antes de publicar. Escenarios sintéticos, sin cambios en el ATS ni reservas reales." actions={<span className="scope-label"><ShieldCheck size={14} /> Entorno de evaluación</span>} />{(personas.error || runs.error || start.error) && <p className="inline-error" role="alert">{(personas.error ?? runs.error ?? start.error)?.message}</p>}
    <label className="form-label">Oferta que se evalúa<select value={offerKey} onChange={e => { setOfferKey(e.target.value); setSelected(null); }}><option value="">Configuración común</option>{offers.data?.offers.filter(o => o.draft).map(o => <option key={o.key} value={o.key}>{o.draft?.title}</option>)}</select><span>Cada oferta abierta necesita sus propias evaluaciones. Para una oferta nueva, desmarca la comparación con la versión publicada.</span></label><Card><CardHeader><CardTitle className="flex items-center gap-2"><FlaskConical size={16} /> Preparar una evaluación</CardTitle><p className="text-xs text-muted-foreground">Selecciona los escenarios y establece un límite de coste.</p></CardHeader><CardContent><form className="space-y-5" onSubmit={e => { e.preventDefault(); start.mutate(); }}><div className="eval-scenario-grid">{personas.data?.map(persona => <label key={persona.key} className={`eval-scenario-choice ${keys.includes(persona.key) ? "is-checked" : ""}`}><Checkbox checked={keys.includes(persona.key)} onCheckedChange={value => setSelected(value === true ? [...keys, persona.key] : keys.filter(k => k !== persona.key))} /><span>{persona.name}<small>{persona.language?.toUpperCase() ?? "ES"} · {verdicts[persona.expected_verdict]}</small></span></label>)}</div><div className="eval-run-options"><label className="flex gap-2 items-center text-xs"><Checkbox checked={compare} onCheckedChange={value => setCompare(value === true)} />Comparar con la versión publicada</label><label className="flex items-center gap-3 text-xs">Coste máximo ($)<Input className="w-24" type="number" min={0.1} max={20} step={0.1} value={budget} onChange={e => setBudget(e.target.value)} /></label><Button disabled={start.isPending || !keys.length || (!offerKey && !!offers.data?.offers.some(o => o.draft?.status === "open"))}><Play size={13} />{start.isPending ? "Encolando…" : "Lanzar evals"}</Button></div></form></CardContent></Card>
    <Card><CardHeader><CardTitle>Ejecuciones</CardTitle><p className="text-xs text-muted-foreground">Resultados, coste y comparación con el umbral de publicación.</p></CardHeader><CardContent><Table><TableHeader><TableRow>{["Fecha", "Rev", "Estado", "Borrador / publicada", "Umbral", "Coste"].map(label => <TableHead key={label}>{label}</TableHead>)}</TableRow></TableHeader><TableBody>{runs.data?.map(run => <TableRow key={run.id}><TableCell><Link className="underline underline-offset-4" to={`/t/${tenantId}/evals/${run.id}`}>{new Date(run.created_at).toLocaleString("es")}</Link></TableCell><TableCell>{run.draft_rev}</TableCell><TableCell><StatusBadge status={run.status} /></TableCell><TableCell><span className="font-medium text-foreground">{run.score_draft?.toFixed(1) ?? "—"}</span> / {run.score_published?.toFixed(1) ?? "—"}</TableCell><TableCell>{typeof run.summary.threshold === "number" ? run.summary.threshold : "No registrado"}</TableCell><TableCell>{cost(run.cost_usd_micros)}</TableCell></TableRow>)}</TableBody></Table>{runs.isPending && <p role="status" className="table-loading">Cargando ejecuciones…</p>}{runs.data?.length === 0 && <EmptyState compact title="Todavía no hay ejecuciones" description="Selecciona tus escenarios y lanza la primera evaluación." />}</CardContent></Card>
    <details className="surface disclosure-panel"><summary><Users size={15} /><span>Detalle de los candidatos sintéticos</span><ChevronDown size={14} className="disclosure-chevron" /></summary><div className="disclosure-content grid gap-3 md:grid-cols-2">{personas.data?.map(persona => <article className="eval-persona" key={persona.key}><h3>{persona.name} <span className="version-chip ml-2">{persona.language?.toUpperCase() ?? "ES"}</span></h3><p>{persona.facts}</p><small>Resultado esperado: {verdicts[persona.expected_verdict]}</small></article>)}</div></details></div>;
}
