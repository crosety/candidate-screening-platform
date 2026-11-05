import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { api } from "@screening/api-client";
import { ArrowUpRight, CheckCheck, MessageSquare, Search, Users } from "lucide-react";
import { useTenant } from "@/layout/AppLayout";
import { Avatar, EmptyState, MetricCard, PageHeader, StatusBadge } from "@/components/WorkspaceUI";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { apiError } from "@/lib/errors";

export function moment(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString("es-ES", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) : "—";
}

export function WorkspacePage({ inbox = false, simulation = false }: { inbox?: boolean; simulation?: boolean }) {
  const { tenantId, tenant } = useTenant();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const offer = params.get("offer") ?? "";
  const offers = useQuery({ queryKey: ["offers", tenantId], queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/offers", { params: { path: { tenant_id: tenantId } } });
    if (!r.data) throw new Error(apiError(r.error)); return r.data;
  }});
  const list = useQuery({ queryKey: ["workspace", tenantId, inbox, simulation, offer, search, status, offset], refetchInterval: 5000, queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/workspace", { params: { path: { tenant_id: tenantId }, query: { environment: simulation ? "simulation" : "production", attention: inbox, offer, search, status, offset } } });
    if (!r.data) throw new Error(apiError(r.error)); return r.data;
  }});
  return <div className="space-y-6">
    {!simulation && <PageHeader eyebrow="Selección" title={inbox ? "Tu atención, donde hace falta" : "Candidaturas"} description={inbox ? "Conversaciones derivadas y evaluaciones pendientes. Cada caso, con un siguiente paso claro." : "Consulta las conversaciones, los datos recogidos y el estado de cada candidatura."} actions={<span className="scope-label">Actualización automática</span>} />}
    {!simulation && <div className="metrics-grid"><MetricCard label="Necesitan atención" value={list.data?.attention_count} description="Derivaciones y decisiones pendientes" icon={MessageSquare} /><MetricCard label="En conversación" value={list.data?.active_count} description="El asistente está atendiendo" kind="active" /><MetricCard label="Candidaturas recibidas" value={list.data?.applications_count} description={`${list.data?.awaiting_start_count ?? "—"} aún sin iniciar conversación`} kind="neutral" icon={Users} /><MetricCard label="Conversaciones cerradas" value={list.data?.completed_count} description="No equivale a contratación o descarte" kind="completed" icon={CheckCheck} /></div>}
    <section className="surface work-queue"><div className="panel-heading"><div><h2>{simulation ? "Conversaciones de simulación" : inbox ? "Pendientes del equipo" : "Conversaciones"} <span className="count-pill">{list.data?.total ?? "—"}</span></h2><p>{inbox ? "Abre un caso para responder, llamar o revisar la evaluación." : "Cada conversación conserva su oferta y su historial."}</p></div></div>
      <div className="candidate-filters"><div className="search-field"><Search size={15} /><Input aria-label="Buscar candidato" placeholder="Nombre o teléfono…" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></div><label className="select-field"><select aria-label="Filtrar por oferta" value={offer} onChange={e => { const next = new URLSearchParams(params); e.target.value ? next.set("offer", e.target.value) : next.delete("offer"); setParams(next); setOffset(0); }}><option value="">Todas las ofertas</option>{offers.data?.offers.map(o => <option key={o.key} value={o.key}>{o.title}</option>)}</select></label><label className="select-field"><select aria-label="Filtrar por estado" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}><option value="">Todos los estados</option>{!inbox && <option value="awaiting">Sin iniciar</option>}<option value="handed_off">Con el equipo</option><option value="active">En conversación</option><option value="completed">Completadas</option><option value="abandoned">Cerradas por inactividad</option></select></label></div>
      {(search || status || offer) && <Button className="mx-5 mb-3" variant="ghost" size="sm" onClick={() => { setSearch(""); setStatus(""); setOffset(0); const next = new URLSearchParams(params); next.delete("offer"); setParams(next); }}>Limpiar filtros</Button>}
      {(list.error || offers.error) && <p className="inline-error m-5" role="alert">{(list.error ?? offers.error)?.message}<Button variant="ghost" onClick={() => { void list.refetch(); void offers.refetch(); }}>Reintentar</Button></p>}
      {list.isPending && <p className="table-loading" role="status">Cargando conversaciones…</p>}
      {list.data?.awaiting.map(row => <article className="work-queue-row" key={row.id}><Avatar name={row.name} /><div className="queue-person"><h3>{row.name}</h3><p>{offers.data?.offers.find(o => o.key === row.offer_key)?.title ?? row.offer_key}</p>{row.phone && <a href={`tel:${row.phone.replace(/[^+\d]/g, "")}`}>{row.phone}</a>}</div><div className="queue-action"><strong>Sin iniciar conversación</strong><span>Esperando al candidato</span></div><div className="queue-time"><time dateTime={row.received_at}>{moment(row.received_at)}</time><span>Candidatura recibida</span></div></article>)}
      {list.data?.rows.map(row => <Link className="work-queue-row" to={`/t/${tenantId}/candidaturas/${row.id}`} key={row.id}><Avatar name={row.name} /><div className="queue-person"><h3>{row.name}</h3><p>{row.offer_title}</p><p className="queue-preview">{row.last_message}</p></div><div className="queue-action"><div className="flex flex-wrap gap-2">{row.attention.includes("human") && <span className="attention-badge"><MessageSquare size={12} />Atención humana</span>}{row.attention.includes("decision") && <span className="attention-badge secondary">Evaluación pendiente</span>}{!row.attention.length && <StatusBadge status={row.status} />}</div><strong>{row.next_step}</strong><span>{row.owner ? `Atiende ${row.owner}` : row.status === "handed_off" ? "Sin asignar" : ""}</span></div><div className="queue-time"><time dateTime={row.last_interaction_at}>{moment(row.last_interaction_at)}</time><span>Última interacción</span>{row.handed_off_at && row.status === "handed_off" && <small>Derivada {moment(row.handed_off_at)}</small>}</div><ArrowUpRight className="queue-arrow" size={18} /></Link>)}
      {list.data?.total === 0 && <EmptyState title={search || status || offer ? "No hay coincidencias" : inbox ? "No hay casos pendientes" : "Aún no hay conversaciones"} description={search || status || offer ? "Prueba otros filtros para encontrar la candidatura." : inbox ? "Las peticiones de atención humana y las evaluaciones por revisar aparecerán aquí." : "Las candidaturas aparecerán aquí cuando empiece su conversación."} />}
      {!!list.data?.total && <div className="queue-pagination"><span>{offset + 1}–{offset + list.data.rows.length + list.data.awaiting.length} de {list.data.total} {status === "awaiting" ? "candidaturas" : "conversaciones"}</span><Button variant="outline" size="sm" disabled={!offset} onClick={() => setOffset(n => Math.max(0, n - 40))}>Anterior</Button><Button variant="outline" size="sm" disabled={offset + 40 >= list.data.total} onClick={() => setOffset(n => n + 40)}>Siguiente</Button></div>}
    </section>
    {!simulation && tenant.role !== "recruiter" && <p className="workspace-note">Las pruebas se consultan en <Link to={`/t/${tenantId}/simulacion`}>Simulación</Link> y no cuentan como actividad de selección.</p>}
  </div>;
}
