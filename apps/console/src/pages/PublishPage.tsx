import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type components } from "@screening/api-client";
import { Link } from "react-router";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ArrowUpRight, Check, CircleDashed, FileCheck2, ShieldCheck } from "lucide-react";
import { PageHeader } from "@/components/WorkspaceUI";

type Publication = components["schemas"]["PublicationOut"];
const severity = { error: "Error", warning: "Aviso", info: "Información" };

export function PublishPage() {
  const { tenantId, tenant } = useTenant();
  const cache = useQueryClient();
  const [note, setNote] = useState("");
  const [percent, setPercent] = useState("100");
  const [segment, setSegment] = useState("");
  const [lastPublication, setLastPublication] = useState<Publication | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pending = useQuery({ queryKey: ["publications", tenantId], refetchInterval: 3000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/publications", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const publication = pending.data?.find(p => p.id === selectedId) ?? pending.data?.[0] ?? lastPublication;
  const gate = useQuery({ queryKey: ["gate", tenantId], refetchInterval: 3000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/draft/gate", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  const checks = useQuery({ queryKey: ["checks", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/draft/checks", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  function confirmed(data: Publication) {
    setLastPublication(data);
    setSelectedId(data.id);
    for (const key of ["draft", "offers", "versions", "diff", "checks", "gate", "publications"]) void cache.invalidateQueries({ queryKey: [key, tenantId] });
    void cache.invalidateQueries({ queryKey: ["tenants"] });
  }
  const publish = useMutation({ mutationFn: async () => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/publications", { params: { path: { tenant_id: tenantId } }, body: { note, percent: Number(percent), segment: segment.trim() } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: confirmed });
  const approve = useMutation({ mutationFn: async (kind: "second_fde" | "client") => {
    if (!publication) throw new Error("No hay publicación pendiente.");
    const result = await api.POST("/api/v1/tenants/{tenant_id}/publications/{publication_id}/approvals", { params: { path: { tenant_id: tenantId, publication_id: publication.id } }, body: { kind } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: confirmed });
  const cancel = useMutation({ mutationFn: async () => {
    if (!publication) throw new Error("No hay publicación pendiente.");
    const result = await api.POST("/api/v1/tenants/{tenant_id}/publications/{publication_id}/cancel", { params: { path: { tenant_id: tenantId, publication_id: publication.id } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: confirmed });
  const error = publish.error ?? approve.error ?? cancel.error ?? checks.error ?? gate.error ?? pending.error;
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Calidad y versiones" title="Publicar una versión" description="Comprueba el borrador y decide a qué porcentaje de las nuevas conversaciones se aplicará." actions={<Button variant="outline" asChild><Link to={`/t/${tenantId}/cambios`}>Ver cambios<ArrowUpRight size={14} /></Link></Button>} />{error && <Alert variant="destructive"><AlertDescription>{error.message}</AlertDescription></Alert>}
    <div className="release-grid"><div className="space-y-5"><Card><CardHeader><div className="section-step"><span>01</span><div><CardTitle>Comprobaciones del borrador</CardTitle><p>Configuración, conexiones y permisos.</p></div><FileCheck2 size={20} /></div></CardHeader><CardContent className="release-checks">{checks.isPending && <p role="status">Comprobando…</p>}{checks.data?.map(check => <div key={`${check.code}:${check.message}`} className={`release-check check-${check.severity}`}><Badge variant={check.severity === "error" ? "destructive" : "secondary"}>{check.code} · {severity[check.severity]}</Badge><p>{check.message}</p></div>)}</CardContent></Card>
    <Card><CardHeader><div className="section-step"><span>02</span><div><CardTitle>Calidad de la conversación</CardTitle><p>Evaluaciones del borrador · revisión {tenant.draft_rev}.</p></div><ShieldCheck size={20} /></div></CardHeader><CardContent><div className="gate-summary">{gate.isPending ? <p role="status">Consultando evaluaciones…</p> : gate.data?.passed ? <><Check size={22} /><div><strong>Evaluaciones aprobadas</strong><p>Puntuación {gate.data.score} / 100 · umbral {gate.data.threshold}</p></div></> : <><CircleDashed size={22} /><div><strong>{gate.isError ? "No se ha podido comprobar la calidad" : "Evaluación pendiente de aprobar"}</strong><p>{gate.isError ? "Vuelve a cargar la página antes de publicar." : "Faltan evals aprobadas para este borrador."}{gate.data && !gate.data.required && " Control desactivado en este entorno."}</p></div></>}</div><Button className="mt-4" variant="outline" asChild><Link to={`/t/${tenantId}/evals`}>Ir a Evals<ArrowUpRight size={14} /></Link></Button></CardContent></Card></div>
    <Card className="release-settings"><CardHeader><div className="section-step"><span>03</span><div><CardTitle>Alcance de la publicación</CardTitle><p>Una versión nueva e inmutable.</p></div></div></CardHeader><CardContent className="space-y-5"><div className="space-y-2"><Label htmlFor="publication-note">Nota de la versión</Label><Input id="publication-note" maxLength={300} placeholder="Qué cambia en esta versión…" value={note} onChange={e => setNote(e.target.value)} /></div><div className="space-y-2"><Label>Sesiones nuevas del piloto</Label><Select value={percent} onValueChange={setPercent}><SelectTrigger aria-label="Porcentaje del piloto"><SelectValue /></SelectTrigger><SelectContent>{[10, 25, 50, 100].map(n => <SelectItem key={n} value={String(n)}>{n} %</SelectItem>)}</SelectContent></Select></div><div className="release-note"><ShieldCheck size={17} /><p>Las conversaciones en curso mantienen su versión. Los cambios que lo requieran quedarán pendientes de aprobación.</p></div><Button className="w-full" disabled={publish.isPending || checks.isPending || checks.isError || gate.isPending || gate.isError || (gate.data?.required && !gate.data.passed) || checks.data?.some(c => c.severity === "error")} onClick={() => { approve.reset(); publish.mutate(); }}>{publish.isPending ? "Publicando…" : "Publicar"}</Button></CardContent></Card></div>
    <div className="space-y-2"><Label htmlFor="pilot-segment">Segmento del piloto (opcional)</Label><Input id="pilot-segment" value={segment} maxLength={60} onChange={e => setSegment(e.target.value)} placeholder="Vacío: todos los segmentos" /><p className="text-sm text-muted-foreground">Usa un segmento configurado en Conexiones. Se aplican las mismas aprobaciones; la versión general no cambia.</p></div>
    {pending.data && pending.data.length > 1 && <div className="flex flex-wrap gap-2" aria-label="Publicaciones pendientes">{pending.data.map(p => <Button key={p.id} variant={p.id === publication?.id ? "default" : "outline"} onClick={() => setSelectedId(p.id)}>Versión {p.version} · {p.segment || "General"}</Button>)}</div>}
    {publication && <Card><CardHeader><CardTitle>Publicación {publication.version}</CardTitle><p className="text-sm text-muted-foreground">Pendientes compartidas entre usuarios y navegadores. El servidor valida cada aprobación.</p></CardHeader><CardContent className="space-y-4"><p role="status">{publication.state === "active" ? `Activa al ${publication.percent} %` : publication.state === "pending_approval" ? "Pendiente de aprobación" : publication.state === "cancelled" ? "Cancelada" : "Sustituida"}</p>{publication.state === "pending_approval" && <div className="flex flex-wrap gap-2">{publication.needs_second_approval && !publication.approvals.includes("second_fde") && <Button variant="outline" disabled={approve.isPending || cancel.isPending} onClick={() => approve.mutate("second_fde")}>Aprobar como segundo FDE</Button>}{publication.needs_client_approval && !publication.approvals.includes("client") && <Button variant="outline" disabled={approve.isPending || cancel.isPending} onClick={() => approve.mutate("client")}>Marcar aprobación del cliente (confirmada fuera de la plataforma)</Button>}<Button variant="outline" disabled={approve.isPending || cancel.isPending} onClick={() => cancel.mutate()}>Cancelar publicación</Button></div>}</CardContent></Card>}
  </div>;
}
