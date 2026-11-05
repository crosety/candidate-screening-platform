import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { api, type components } from "@screening/api-client";
import { ArrowLeft, ArrowUpRight, BriefcaseBusiness, Plus, Settings2 } from "lucide-react";
import { useTenant } from "@/layout/AppLayout";
import { EmptyState, PageHeader } from "@/components/WorkspaceUI";
import { Button } from "@/components/ui/button";
import { OfferEditor } from "@/components/OfferEditor";
import { apiError } from "@/lib/errors";

type Definition = components["schemas"]["OfferDefinition"];
const labels: Record<string, string> = { open: "Abierta", draft: "Borrador", paused: "Pausada", closed: "Cerrada", legacy: "Configuración común" };

function ApplicationLink({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return <div className="offer-application-link"><p>Enlace para recibir candidaturas</p><div><Button size="sm" variant="outline" asChild><a href={url} target="_blank" rel="noreferrer">Abrir oferta<ArrowUpRight size={14} /></a></Button><Button size="sm" variant="ghost" onClick={async () => { try { await navigator.clipboard.writeText(url); setCopied(true); setFailed(false); } catch { setFailed(true); } }}>{copied ? "Enlace copiado" : "Copiar enlace"}</Button></div>{failed && <input aria-label="Enlace de la oferta" value={url} readOnly onFocus={e => e.target.select()} />}<small>Cada nuevo inicio crea una candidatura independiente.</small></div>;
}

export function useOffers() {
  const { tenantId } = useTenant();
  return useQuery({ queryKey: ["offers", tenantId], queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/offers", { params: { path: { tenant_id: tenantId } } });
    if (!r.data) throw new Error(apiError(r.error)); return r.data;
  }});
}

export function OffersPage() {
  const { tenantId, tenant } = useTenant();
  const query = useOffers();
  return <div className="space-y-6"><PageHeader eyebrow="Selección" title="Ofertas" description="Cada oferta reúne su contexto y sus candidaturas. Puede cubrir un puesto, un centro o una combinación de ambos." actions={tenant.role !== "recruiter" && <Button asChild><Link to={`/t/${tenantId}/ofertas/crear/nueva`}><Plus size={15} />Crear oferta</Link></Button>} />{query.error && <p role="alert" className="inline-error">{query.error.message}</p>}{query.isPending && <p role="status">Cargando ofertas…</p>}<div className="offer-grid">{query.data?.offers.map(offer => <article className="surface offer-card" key={offer.key}><div className="offer-card-top"><span className="offer-icon"><BriefcaseBusiness size={20} /></span><span className={`offer-status offer-status-${offer.status}`}>{labels[offer.status]}</span></div><h2>{offer.title}</h2><p className="offer-description">{offer.description}</p>{Object.keys(offer.dimensions).length > 0 && <div className="offer-dimensions">{Object.entries(offer.dimensions).map(([key, value]) => <span key={key}>{key}: {value}</span>)}</div>}<div className="offer-counts"><div><strong>{offer.applications}</strong><span>Candidaturas</span></div><div><strong>{offer.attention}</strong><span>Necesitan atención</span></div></div>{offer.application_url && <ApplicationLink url={offer.application_url} />}{offer.has_changes && <p className="draft-note">Cambios guardados, pendientes de publicar</p>}<footer><Button variant="outline" asChild><Link to={`/t/${tenantId}/candidaturas?offer=${encodeURIComponent(offer.key)}`}>Ver candidaturas<ArrowUpRight size={14} /></Link></Button>{tenant.role !== "recruiter" && <Link className="offer-edit" to={`/t/${tenantId}/ofertas/${encodeURIComponent(offer.key)}`}><Settings2 size={14} />Configurar</Link>}</footer></article>)}</div>{query.data?.offers.length === 0 && <div className="surface"><EmptyState title="Todavía no hay ofertas" description={tenant.role === "recruiter" ? "El equipo de configuración preparará las ofertas de este cliente." : "Crea una oferta y define la información que necesita recoger el asistente."} /></div>}{tenant.role !== "recruiter" && <p className="workspace-note">El contexto común, las conexiones y los permisos se gestionan en <Link to={`/t/${tenantId}/perfil`}>Configuración del cliente</Link>. Guardar una oferta no la publica.</p>}</div>;
}

export function OfferEditorPage() {
  const { tenantId, tenant } = useTenant();
  const { offerKey } = useParams();
  const query = useOffers();
  if (tenant.role === "recruiter") return <p role="alert">Solo el equipo de configuración puede editar ofertas.</p>;
  if (query.error) return <p role="alert" className="inline-error">{query.error.message}</p>;
  if (!query.data) return <p role="status">Cargando configuración…</p>;
  const item = query.data.offers.find(o => o.key === offerKey);
  if (offerKey !== undefined && !item) return <p role="alert">No se ha encontrado esta oferta.</p>;
  const initial: Definition = item?.draft ?? item?.published ?? { key: offerKey ?? "", title: item?.title ?? "", status: "draft", description: "", instructions: "", fields: [], fields_mode: "custom", source_ids: [], criteria: [], capabilities: null, dimensions: {} };
  return <div className="space-y-6"><Link className="workspace-back" to={`/t/${tenantId}/ofertas`}><ArrowLeft size={14} />Todas las ofertas</Link><PageHeader eyebrow="Configuración FDE" title={offerKey === undefined ? "Nueva oferta" : initial.title} description="Define el puesto, las preguntas y el criterio de evaluación. El contexto común se hereda del cliente." /><OfferEditor key={offerKey} initial={initial} source={query.data} creating={offerKey === undefined} /></div>;
}
