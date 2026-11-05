import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@screening/api-client";
import { EmptyState, PageHeader } from "@/components/WorkspaceUI";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

const labels: Record<string, string> = { proposed: "Pendiente de aprobación", scheduled: "Programado", sent: "Enviado", cancelled: "Cancelado", blocked: "Bloqueado por política", expired: "Caducado", failed: "Incidencia de envío" };

export function FollowupsPage() {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const query = useQuery({ queryKey: ["followups", tenantId], refetchInterval: 5000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/followups", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const action = useMutation({ mutationFn: async ({ id, kind }: { id: string; kind: "approve" | "cancel" }) => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/followups/{followup_id}", { params: { path: { tenant_id: tenantId, followup_id: id } }, body: { action: kind } });
    if (!result.data) throw new Error(apiError(result.error));
    await cache.invalidateQueries({ queryKey: ["followups", tenantId] });
  }});
  const error = action.error || query.error;
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Operación" title="Reenganche" description="Revisa los recordatorios propuestos y programados. Una respuesta, baja, cierre o derivación cancela los pendientes." />
    {error && <Alert variant="destructive"><AlertDescription>{error.message}</AlertDescription></Alert>}
    <Card><CardContent><Table><TableHeader><TableRow><TableHead>Candidatura</TableHead><TableHead>Fecha</TableHead><TableHead>Motivo</TableHead><TableHead>Estado</TableHead><TableHead>Acciones</TableHead></TableRow></TableHeader><TableBody>{query.data?.map(row => <TableRow key={row.id}>
      <TableCell><Link className="underline" to={`/t/${tenantId}/conversaciones/${row.session_id}`}>{row.candidate_name}</Link>{row.simulated && <Badge className="ml-2" variant="secondary">Simulado</Badge>}</TableCell>
      <TableCell>{new Date(row.run_at).toLocaleString("es-ES", { timeZoneName: "short" })}</TableCell><TableCell>{row.reason}</TableCell><TableCell>{labels[row.status] ?? row.status}</TableCell>
      <TableCell><div className="flex gap-2">{row.status === "proposed" && <Button size="sm" disabled={action.isPending} onClick={() => action.mutate({ id: row.id, kind: "approve" })}>Aprobar</Button>}{["proposed", "scheduled"].includes(row.status) && <Button size="sm" variant="outline" disabled={action.isPending} onClick={() => action.mutate({ id: row.id, kind: "cancel" })}>Cancelar</Button>}</div></TableCell>
    </TableRow>)}</TableBody></Table>{query.isPending && <p className="table-loading" role="status">Cargando recordatorios…</p>}{query.data?.length === 0 && <EmptyState title="No hay recordatorios" description="Puedes probar la propuesta y aprobación desde el teléfono simulado." />}</CardContent></Card>
    <p className="text-xs text-muted-foreground">Los recordatorios por silencio solo se programan si la empresa tiene activada su política de reenganche, que fija número, horario y abandono; los que propone el agente respetan los límites de la plataforma. El silencio aplaza, no descarta a nadie. La aprobación humana no evita las comprobaciones al enviar.</p>
  </div>;
}
