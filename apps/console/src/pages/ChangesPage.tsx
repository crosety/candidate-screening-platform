import { useQuery } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { riskLabels } from "@/lib/profile";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { EmptyState, PageHeader } from "@/components/WorkspaceUI";

const areas: Record<string, string> = { brief: "Brief", operating_model: "Modelo operativo", sources: "Fuentes", channels: "Canales", connections: "Conexiones", grants: "Capacidades", result_schema: "Resultado", autonomy: "Dial", rubric: "Rúbrica", eval_threshold: "Evals" };

export function ChangesPage() {
  const { tenantId, tenant } = useTenant();
  const diff = useQuery({ queryKey: ["diff", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/draft/diff", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  if (diff.isError) return <Alert variant="destructive"><AlertDescription>{diff.error.message}</AlertDescription></Alert>;
  if (!diff.data) return <p>Cargando cambios…</p>;
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Calidad y versiones" title="Cambios frente a la versión activa" description="Compara el borrador con lo publicado y revisa el riesgo de cada cambio antes de continuar." /><div className="change-summary"><Badge variant="secondary">{diff.data.changes.length} cambios</Badge><Badge variant="outline">Riesgo {riskLabels[diff.data.risk]}</Badge>{diff.data.needs_second_approval && <Badge>Necesita segunda aprobación</Badge>}{diff.data.needs_client_approval && <Badge>Necesita aprobación de {tenant.name}</Badge>}</div>
    {!diff.data.changes.length && <section className="surface"><EmptyState title="Todo está al día" description="El borrador coincide con la versión activa. Los cambios que guardes aparecerán aquí." /></section>}
    {[...new Set(diff.data.changes.map(c => c.area))].map(area => <Card key={area}><CardHeader><CardTitle>{areas[area] ?? area}</CardTitle></CardHeader><CardContent className="space-y-5">{diff.data!.changes.filter(c => c.area === area).map((change, index) => change.sentences ? <div key={index}><h3 className="mb-2 font-medium">{change.label}</h3><div className="space-y-1 text-sm leading-relaxed">{change.sentences.map(([kind, sentence], n) => <p key={n} className={kind === "+" ? "bg-emerald-50 p-2 text-emerald-800" : kind === "-" ? "bg-red-50 p-2 text-red-800 line-through" : "p-2"}>{kind === "+" ? "+ " : kind === "-" ? "− " : ""}{sentence}</p>)}</div></div> : <Table key={index}><TableHeader><TableRow><TableHead>Qué</TableHead><TableHead>Antes</TableHead><TableHead>Ahora</TableHead><TableHead>Riesgo</TableHead></TableRow></TableHeader><TableBody><TableRow><TableCell>{change.label}</TableCell><TableCell className="max-w-72 whitespace-pre-wrap break-words">{change.before || "—"}</TableCell><TableCell className="max-w-72 whitespace-pre-wrap break-words">{change.after || "—"}</TableCell><TableCell>{riskLabels[change.risk]}</TableCell></TableRow></TableBody></Table>)}</CardContent></Card>)}
  </div>;
}
