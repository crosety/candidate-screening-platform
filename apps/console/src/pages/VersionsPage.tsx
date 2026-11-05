import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { riskLabels } from "@/lib/profile";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PageHeader, EmptyState } from "@/components/WorkspaceUI";

export function VersionsPage() {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [selected, setSelected] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const versions = useQuery({ queryKey: ["versions", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/versions", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const rollback = useMutation({ mutationFn: async (id: string) => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/versions/{version_id}/rollback", { params: { path: { tenant_id: tenantId, version_id: id } }, body: { note } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: () => {
    setSelected(null); setNote("");
    for (const key of ["draft", "offers", "versions", "diff", "checks", "gate", "publications"]) void cache.invalidateQueries({ queryKey: [key, tenantId] });
    void cache.invalidateQueries({ queryKey: ["tenants"] });
  }});
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Calidad y versiones" title="Versiones" description="Historial de configuraciones publicadas. Cada conversación conserva la versión con la que empezó." />{(versions.error || rollback.error) && <Alert variant="destructive"><AlertDescription>{(versions.error ?? rollback.error)?.message}</AlertDescription></Alert>}{rollback.isSuccess && <p role="status" className="text-sm text-emerald-700">Se ha creado y activado la versión {rollback.data.version}.</p>}{versions.isPending && <p role="status">Cargando versiones…</p>}
    <div className="version-history">{versions.data?.map(version => <Card key={version.id} className={`version-entry ${version.active_percent !== null ? "is-current" : ""}`}><CardHeader><div className="flex flex-wrap items-center gap-3"><CardTitle>Versión {version.version}</CardTitle><Badge variant="outline">Riesgo {riskLabels[version.risk as keyof typeof riskLabels] ?? version.risk}</Badge>{version.active_percent !== null && <Badge>Activa al {version.active_percent} %</Badge>}</div><p className="text-sm text-muted-foreground">{new Date(version.created_at).toLocaleString("es")} · {version.published_by}</p></CardHeader><CardContent className="space-y-4"><p>{version.note || "Sin nota"}</p>{version.active_percent === null && version.can_rollback && (selected !== version.id ? <Button variant="outline" onClick={() => { setSelected(version.id); setNote(""); rollback.reset(); }}>Volver a esta versión</Button> : <div className="space-y-3 rounded-md border p-4"><p className="text-sm">Se creará una versión nueva con este contenido y se activará al 100 %. El historial no se modifica.</p><Label htmlFor={`rollback-${version.id}`}>Motivo de la vuelta atrás</Label><Input id={`rollback-${version.id}`} maxLength={300} value={note} onChange={e => setNote(e.target.value)} /><div className="flex flex-wrap gap-2"><Button disabled={!note.trim() || rollback.isPending} onClick={() => rollback.mutate(version.id)}>Confirmar vuelta atrás</Button><Button variant="ghost" disabled={rollback.isPending} onClick={() => setSelected(null)}>Cancelar</Button></div></div>)}</CardContent></Card>)}</div>{versions.data?.length === 0 && <EmptyState title="Aún no hay versiones" description="Las versiones aparecerán aquí cuando publiques el primer borrador." />}
  </div>;
}
