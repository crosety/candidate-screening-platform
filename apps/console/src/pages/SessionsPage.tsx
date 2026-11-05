import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { cost, sessionLabels } from "@/lib/sessions";
import { ArrowUpRight, Search } from "lucide-react";
import { Avatar, EmptyState, MetricCard, PageHeader, StatusBadge } from "@/components/WorkspaceUI";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

export function SessionsPage() {
  const { tenantId } = useTenant();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const sessions = useQuery({ queryKey: ["sessions", tenantId], refetchInterval: 5000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/sessions", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const visible = sessions.data?.filter(session => (!status || session.status === status) && session.candidate_name.toLocaleLowerCase("es").includes(search.trim().toLocaleLowerCase("es")));
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Operación" title="Conversaciones" description="Sigue cada sesión desde el primer mensaje hasta el resultado. Abre su traza para entender cómo ha actuado el agente." actions={<Button variant="outline" asChild><Link to={`/t/${tenantId}/ats`}>Abrir ATS simulado<ArrowUpRight size={14} /></Link></Button>} />{sessions.error && <Alert variant="destructive"><AlertDescription>{sessions.error.message}</AlertDescription></Alert>}
    <div className="metrics-grid"><MetricCard label="Sesiones cargadas" value={sessions.data?.length} description="Listado de conversaciones" /><MetricCard label="En curso" value={sessions.data?.filter(s => s.status === "active").length} description="Conversaciones activas" kind="active" /><MetricCard label="Completadas" value={sessions.data?.filter(s => s.status === "completed").length} description="Conversación finalizada" kind="completed" /><MetricCard label="Derivadas" value={sessions.data?.filter(s => s.status === "handed_off").length} description="Continuación con el equipo" kind="handed_off" /></div>
    <section className="surface session-list"><div className="panel-heading"><div><h2>Registro de conversaciones <span className="count-pill">{visible?.length ?? "—"}</span></h2><p>Cada sesión conserva la versión con la que comenzó.</p></div><span className="scope-label">Actualización automática</span></div><div className="candidate-filters"><div className="search-field"><Search size={14} /><Input aria-label="Buscar conversación" placeholder="Buscar por nombre…" value={search} onChange={e => setSearch(e.target.value)} /></div><label className="select-field"><select aria-label="Estado de conversación" value={status} onChange={e => setStatus(e.target.value)}><option value="">Todos los estados</option>{Object.entries(sessionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>{(search || status) && <Button variant="ghost" size="sm" onClick={() => { setSearch(""); setStatus(""); }}>Limpiar</Button>}</div>
    {sessions.isPending ? <p className="table-loading" role="status">Cargando conversaciones…</p> : <Table><TableHeader><TableRow><TableHead>Candidato</TableHead><TableHead>Estado</TableHead><TableHead>Versión</TableHead><TableHead>Mensajes</TableHead><TableHead>Coste</TableHead><TableHead>Inicio</TableHead><TableHead><span className="sr-only">Abrir</span></TableHead></TableRow></TableHeader><TableBody>{visible?.map(session => <TableRow key={session.id}><TableCell><Link className="candidate-name" to={`/t/${tenantId}/conversaciones/${session.id}`}><Avatar name={session.candidate_name} /><span><strong>{session.candidate_name}</strong><span>Ver conversación y traza</span></span></Link></TableCell><TableCell><StatusBadge status={session.status} /></TableCell><TableCell><span className="version-chip">v{session.version}</span></TableCell><TableCell>{session.messages}</TableCell><TableCell>{cost(session.cost_usd_micros)}</TableCell><TableCell>{new Date(session.created_at).toLocaleDateString("es", { day: "numeric", month: "short" })}</TableCell><TableCell><Link className="table-open-link" aria-label={`Abrir conversación de ${session.candidate_name}`} to={`/t/${tenantId}/conversaciones/${session.id}`}><ArrowUpRight size={16} /></Link></TableCell></TableRow>)}</TableBody></Table>}
    {visible?.length === 0 && <EmptyState title={sessions.data?.length ? "No hay coincidencias" : "Todavía no hay conversaciones"} description={sessions.data?.length ? "Prueba otro nombre o limpia los filtros." : "Abre un enlace desde el ATS simulado para iniciar una sesión."} />}</section>
  </div>;
}
