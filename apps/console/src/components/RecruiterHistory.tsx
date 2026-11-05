import { useQuery } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { ChevronDown, History } from "lucide-react";
export function RecruiterHistory({ tenantId }: { tenantId: string }) {
  const history = useQuery({ queryKey: ["recruiter-history", tenantId], refetchInterval: 5000, queryFn: async () => (await api.GET("/api/v1/tenants/{tenant_id}/recruiter/history", { params: { path: { tenant_id: tenantId } } })).data as { id: string; created_at: string; payload: { question: string; reply: string } }[] | undefined });
  return <details className="surface disclosure-panel"><summary><History size={15} /><span>Historial privado de consultas</span><ChevronDown size={14} className="disclosure-chevron" /></summary><div className="disclosure-content">{history.isError ? <p role="alert">No se ha podido cargar el historial.</p> : history.data?.length ? history.data.map(h => <article key={h.id} className="history-entry"><p className="text-xs text-muted-foreground">{new Date(h.created_at).toLocaleString("es")}</p><p className="font-medium">{h.payload.question}</p><p className="whitespace-pre-wrap">{h.payload.reply}</p></article>) : <p className="text-muted-foreground">Sin consultas guardadas para tu alcance actual.</p>}</div></details>;
}
