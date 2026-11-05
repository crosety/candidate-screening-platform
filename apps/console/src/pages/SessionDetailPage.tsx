import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router";
import { ExternalLink } from "lucide-react";
import { api } from "@screening/api-client";
import { ResultFields } from "@/components/ResultFields";
import { SentimentPanel } from "@/components/SentimentPanel";
import { MessageObservation, useMessageObservations } from "@/components/MessageObservation";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { capabilityColors, cost } from "@/lib/sessions";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { PageHeader, StatusBadge } from "@/components/WorkspaceUI";
import { stateLabel } from "@/lib/states";

export function SessionDetailPage() {
  const { tenantId } = useTenant();
  const { sessionId = "" } = useParams();
  const detail = useQuery({ queryKey: ["session", tenantId, sessionId], refetchInterval: query => query.state.data?.session.status === "active" ? 3000 : false, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/sessions/{session_id}", { params: { path: { tenant_id: tenantId, session_id: sessionId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const observations = useMessageObservations(tenantId, sessionId, detail.data?.messages.length ?? 0, detail.data?.session.status === "active");
  if (detail.error) return <Alert variant="destructive"><AlertDescription>{detail.error.message}</AlertDescription></Alert>;
  if (!detail.data) return <p>Cargando conversación…</p>;
  const data = detail.data;
  return <div className="space-y-6 fde-page"><Link className="text-sm text-muted-foreground underline underline-offset-4" to={`/t/${tenantId}/conversaciones`}>Volver a conversaciones</Link><PageHeader eyebrow="Detalle de conversación" title={data.application.candidate_name} description={`${data.application.city} · ${data.application.job_ref}`} actions={<>
      <StatusBadge status={data.session.status} />
      {data.langfuse_url && <Button variant="outline" asChild><a href={data.langfuse_url} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer" aria-describedby="langfuse-help">Abrir en Langfuse<ExternalLink size={16} aria-hidden="true" /><span className="sr-only"> (se abre en otra pestaña)</span></a></Button>}
    </>} />
    {data.langfuse_url && <p id="langfuse-help" className="text-sm text-muted-foreground">Consulta las métricas exportadas de esta sesión; si no se han enviado, no aparecerán datos. Necesitas acceso al proyecto de Langfuse.</p>}
    <div className="inspection-context"><div className="surface"><span>Versión fijada</span><strong>v{data.version.version}</strong></div><div className="surface"><span>Mensajes</span><strong>{data.messages.length}</strong></div><div className="surface"><span>Coste total</span><strong>{cost(data.cost_usd_micros)}</strong></div></div>
    <div className="session-inspection"><Card><CardHeader><CardTitle>Transcripción</CardTitle><p className="text-sm text-muted-foreground">Intervenciones del candidato y respuestas del agente.</p></CardHeader><CardContent><ScrollArea className="h-[65vh] pr-4"><div className="conversation-timeline">{data.messages.map(message => <article key={message.id} className={`transcript-message ${message.direction === "in" ? "from-candidate" : ""}`}><header><strong>{message.direction === "in" ? "Candidato" : "Asistente"}</strong><span>{new Date(message.created_at).toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" })}</span></header><p>{message.text}</p>{message.direction === "in" && <MessageObservation value={observations.get(message.id)} />}</article>)}{!data.messages.length && <p className="text-sm text-muted-foreground">Todavía no hay mensajes.</p>}</div></ScrollArea></CardContent></Card>
    <div className="space-y-5">
      {data.result && <Card><CardHeader><CardTitle>Evaluación y evidencia</CardTitle><StatusBadge status={String(data.result.verdict)} /></CardHeader><CardContent className="space-y-4">{data.result.reason != null && <p>{String(data.result.reason)}</p>}<p className="text-sm text-muted-foreground">{String(data.result.summary)}</p><ResultFields layout="cards" fields={data.result.fields as Record<string, unknown>} />{(data.result.flags as string[]).length > 0 && <p className="review-notice">{(data.result.flags as string[]).map(flag => ({ unclear_requirement: "Requisito por aclarar", prompt_injection_attempt: "Intento de alterar instrucciones", third_party_data_request: "Solicitud de datos ajenos", sensitive_data_shared: "Datos sensibles compartidos" }[flag] ?? flag)).join(" · ")}</p>}<h3 className="font-medium">Decisiones</h3>{data.decisions.map((decision, index) => <p key={index} className="text-sm">{stateLabel(String(decision.decision_type))} · {stateLabel(String(decision.status))} · {decision.decided_by === "agent" ? "Agente" : "Equipo"} {String(decision.user_id ?? "")} · {new Date(String(decision.applied_at ?? decision.created_at)).toLocaleString("es")}</p>)}</CardContent></Card>}
      <SentimentPanel value={data.result?.sentiment} />
      <Card><CardHeader><CardTitle>Llamadas al modelo</CardTitle></CardHeader><CardContent>{data.llm_calls.length ? <Table><TableHeader><TableRow><TableHead>Agente / modelo</TableHead><TableHead>Entrada / salida</TableHead><TableHead>Caché R / W</TableHead><TableHead>Coste</TableHead><TableHead>Latencia</TableHead><TableHead>Fin</TableHead></TableRow></TableHeader><TableBody>{data.llm_calls.map(call => <TableRow key={call.id}><TableCell><p className="font-medium">{call.agent}</p><p className="mt-1 max-w-44 break-words whitespace-normal text-xs text-muted-foreground">{call.model_id}</p></TableCell><TableCell>{call.input_tokens} / {call.output_tokens}</TableCell><TableCell>{call.cache_read_tokens} / {call.cache_write_tokens}</TableCell><TableCell>{cost(call.cost_usd_micros)}</TableCell><TableCell>{call.latency_ms} ms</TableCell><TableCell>{call.stop_reason || "—"}</TableCell></TableRow>)}</TableBody></Table> : <p className="text-sm text-muted-foreground">No hay llamadas al modelo registradas.</p>}</CardContent></Card>
      <Card><CardHeader><CardTitle>Capacidades</CardTitle></CardHeader><CardContent className="space-y-3">{data.capability_calls.map(call => <Collapsible key={call.id} className="rounded-lg border p-3"><CollapsibleTrigger className="flex w-full flex-wrap items-center gap-2 text-left text-sm"><span className="mr-auto font-medium">{call.capability}</span><Badge className={capabilityColors[call.status]} variant="secondary">{call.status}</Badge><span className="text-xs text-muted-foreground">{call.latency_ms} ms · Ver JSON</span></CollapsibleTrigger><CollapsibleContent><p className="mt-4 text-xs text-muted-foreground">Conexión: {call.connection_id || "Sin conexión"}</p><p className="mt-3 text-xs font-medium">Solicitud</p><pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(call.request, null, 2)}</pre><p className="mt-3 text-xs font-medium">Respuesta</p><pre className="mt-1 max-h-60 overflow-auto rounded bg-muted p-3 text-xs">{JSON.stringify(call.response, null, 2)}</pre></CollapsibleContent></Collapsible>)}{!data.capability_calls.length && <p className="text-sm text-muted-foreground">No hay llamadas a capacidades registradas.</p>}</CardContent></Card>
    </div></div>
  </div>;
}
