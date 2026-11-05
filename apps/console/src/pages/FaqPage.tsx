import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { BookOpen, Search } from "lucide-react";
import { EmptyState, PageHeader } from "@/components/WorkspaceUI";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useTenant } from "@/layout/AppLayout";
import { ProfilePage } from "./ProfilePage";
type Version = { version_id: string; version: string; fragments: number; models: string[] };
export function FaqPage() {
  return <div className="space-y-6"><ProfilePage mode="library" /><details className="surface p-5"><summary>Consultar el índice de una versión publicada</summary><div className="mt-6"><PublishedSources /></div></details></div>;
}
function PublishedSources() {
  const { tenantId } = useTenant(); const [selected, setSelected] = useState(""); const [query, setQuery] = useState(""); const [result, setResult] = useState<{ status: string; fragments: { source_name: string; source_id: string; text: string; score: number; citation?: string }[] }>(); const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  const versions = useQuery({ queryKey: ["faq", tenantId], queryFn: async () => { const r = await api.GET("/api/v1/tenants/{tenant_id}/faq", { params: { path: { tenant_id: tenantId } } }); if (!r.data) throw new Error("No se puede cargar el índice."); return r.data as Version[]; } });
  const version = selected || versions.data?.[0]?.version_id || ""; const path = { tenant_id: tenantId, version_id: version };
  async function index() { setBusy(true); setError(""); try { const r = await api.POST("/api/v1/tenants/{tenant_id}/faq/{version_id}/index", { params: { path } }); if (!r.data) throw new Error("No se puede indexar la versión."); await versions.refetch(); } catch (e) { setError(e instanceof Error ? e.message : "Error"); } finally { setBusy(false); } }
  async function search() { setBusy(true); setError(""); try { const r = await api.POST("/api/v1/tenants/{tenant_id}/faq/{version_id}/search", { params: { path }, body: { query } }); if (!r.data) throw new Error("No se puede consultar el índice."); setResult(r.data as typeof result); } catch (e) { setError(e instanceof Error ? e.message : "Error"); } finally { setBusy(false); } }
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Diseño del agente" title="Fuentes y búsqueda" description="Consulta qué información puede recuperar el agente y contrasta sus respuestas con las fuentes de cada versión." />
    {(error || versions.error) && <p className="inline-error" role="alert">{error || versions.error?.message}</p>}
    <section className="surface"><div className="panel-heading"><div><h2>Biblioteca de conocimiento</h2><p>Selecciona una versión para revisar su índice de fuentes.</p></div><BookOpen size={19} className="text-muted-foreground" /></div><div className="p-5 pt-0 space-y-5"><div className="flex flex-wrap items-end gap-3"><div className="flex-1 min-w-0 space-y-2"><Label htmlFor="source-version">Versión del perfil</Label><select id="source-version" aria-label="Versión de fuentes" value={version} onChange={e => { setSelected(e.target.value); setResult(undefined); }} className="w-full rounded border p-2">{versions.data?.map(v => <option key={v.version_id} value={v.version_id}>{v.version} · {v.fragments} fragmentos</option>)}</select></div><Button variant="outline" disabled={busy || !version} onClick={() => void index()}>Indexar fuentes de búsqueda</Button></div>
    <form onSubmit={e => { e.preventDefault(); void search(); }} className="space-y-2"><Label htmlFor="source-question">Pregunta a tus fuentes</Label><div className="flex flex-wrap gap-3"><Input id="source-question" aria-label="Pregunta a las fuentes" value={query} onChange={e => setQuery(e.target.value)} maxLength={500} className="min-w-40 flex-1" placeholder="¿Qué información quieres contrastar?" /><Button disabled={busy || !query.trim() || !version}><Search size={14} />{busy ? "Procesando…" : "Buscar evidencia"}</Button></div></form>
    <details className="text-xs text-muted-foreground"><summary>Detalles del índice</summary><p className="mt-3 leading-relaxed">Modelos: {versions.data?.find(v => v.version_id === version)?.models.join(", ") || "Sin indexar"}. Búsqueda por similitud con pgvector y embeddings. Las pruebas offline usan vectores sintéticos.</p></details></div></section>
    {versions.isPending && <p role="status" className="table-loading">Cargando fuentes…</p>}
    {result ? <section className="space-y-3" aria-live="polite"><div className="section-intro"><div><h2>{result.fragments.length ? "Evidencia encontrada" : "Sin evidencia relevante"}</h2><p>Resultados de la consulta en la versión seleccionada.</p></div><span className="count-pill">{result.fragments.length}</span></div>{result.fragments.map((r, i) => <article key={i} className="surface p-5"><header className="flex items-center justify-between gap-3"><strong className="text-sm font-medium">{r.source_name}</strong><span className="version-chip">Similitud {r.score.toFixed(3)}</span></header><p className="mt-2 text-xs text-muted-foreground">{r.citation || r.source_id}</p><p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed">{r.text}</p></article>)}{!result.fragments.length && <div className="surface"><EmptyState title="No se ha encontrado evidencia" description="Prueba una pregunta más concreta o comprueba las fuentes de esta versión." /></div>}</section> : <section className="surface"><EmptyState title="Contrasta una pregunta" description="Las coincidencias aparecerán aquí con su fuente y fragmento de evidencia." /></section>}
  </div>;
}
