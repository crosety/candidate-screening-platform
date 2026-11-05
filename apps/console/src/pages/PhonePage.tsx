import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { PageHeader, StatusBadge } from "@/components/WorkspaceUI";

export function PhonePage() {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [template, setTemplate] = useState(true);
  const [notice, setNotice] = useState("");
  const candidates = useQuery({ queryKey: ["phone-candidates", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/phone", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const applicationId = selected || candidates.data?.[0]?.id || "";
  const path = { tenant_id: tenantId, application_id: applicationId };
  const state = useQuery({ queryKey: ["phone", tenantId, applicationId], enabled: !!applicationId, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/phone/{application_id}", { params: { path } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const action = useMutation({ mutationFn: async (kind: "message" | "advance" | "advance2" | "schedule" | "outbound") => {
    setNotice("");
    if (kind === "schedule") {
      if (!state.data?.session_id) throw new Error("Primero envía una entrada para abrir la sesión.");
      const at = new Date(new Date(state.data.now).getTime() + 2 * 3600_000).toISOString();
      const result = await api.POST("/api/v1/tenants/{tenant_id}/followups", { params: { path: { tenant_id: tenantId } }, body: { session_id: state.data.session_id, at_local: at, reason: "Retomar la candidatura en dos horas (prueba de teléfono)" } });
      if (!result.data) throw new Error(apiError(result.error));
      setNotice(result.data.status === "proposed" ? "Recordatorio propuesto: requiere aprobación en Reenganche." : `Recordatorio: ${result.data.status}`);
    } else if (kind === "advance" || kind === "advance2") {
      const hours = kind === "advance2" ? 2 : 25;
      const result = await api.POST("/api/v1/tenants/{tenant_id}/phone/{application_id}/advance", { params: { path }, body: { hours } });
      if (!result.data) throw new Error(apiError(result.error));
      setNotice(`Reloj de este teléfono adelantado ${hours} horas. El reloj del agente y los demás candidatos no cambian.`);
    } else if (kind === "message") {
      const result = await api.POST("/api/v1/tenants/{tenant_id}/phone/{application_id}/messages", { params: { path }, body: { text, client_message_id: crypto.randomUUID() } });
      if (!result.data) throw new Error(apiError(result.error));
      setText("");
      setNotice(result.data.status === "opted_out" ? "Baja registrada sin llamar al modelo." : "Entrada procesada por el mismo agente y la misma sesión.");
    } else {
      const result = await api.POST("/api/v1/tenants/{tenant_id}/phone/{application_id}/outbound", { params: { path }, body: { text: "¿Quieres continuar con tu candidatura?", use_template: template } });
      if (!result.data) throw new Error(apiError(result.error));
      const labels: Record<string, string> = { template: "Salida simulada como plantilla: la ventana de 24 horas está cerrada.", text: "Salida simulada como texto dentro de la ventana.", no_approved_template: "Envío bloqueado: fuera de la ventana hace falta una plantilla aprobada.", quiet_hours: "Envío aplazado por horario de silencio (22:00–08:00).", opted_out: "Envío bloqueado: este contacto se ha dado de baja.", session_closed: "Envío bloqueado: la conversación está cerrada." };
      setNotice(labels[result.data.status] ?? result.data.status);
    }
    await cache.invalidateQueries({ queryKey: ["phone", tenantId, applicationId] });
  }});
  const error = action.error || state.error || candidates.error;
  return <div className="space-y-6 fde-page">
    <PageHeader eyebrow="Operación" title="Teléfono simulado" description="Prueba el canal de WhatsApp con la misma conversación y versión del chat web. El agente usa el proveedor configurado para este cliente." actions={<Badge variant="outline">Simulado · Sin envíos externos</Badge>} />
    {error && <Alert variant="destructive"><AlertDescription>{error.message}</AlertDescription></Alert>}
    {notice && <Alert><AlertDescription role="status">{notice}</AlertDescription></Alert>}
    <Label className="block">Candidatura<select aria-label="Candidatura del teléfono" className="mt-2 block w-full rounded-md border bg-background p-3" value={applicationId} onChange={e => { setSelected(e.target.value); setNotice(""); }}>
      {candidates.data?.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.candidate_name} · {candidate.external_id}</option>)}
    </select></Label>
    {!candidates.isPending && !candidates.data?.length && <p>Primero crea una candidatura en el <Link className="underline" to={`/t/${tenantId}/ats`}>ATS simulado</Link>.</p>}
    {state.data && <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
      <Card><CardHeader><div className="flex justify-between"><CardTitle>{state.data.candidate_name}</CardTitle><StatusBadge status={state.data.status} /></div><p className="text-xs text-muted-foreground">{state.data.session_id ? <Link className="underline" to={`/t/${tenantId}/conversaciones/${state.data.session_id}`}>Ver sesión, versión y trazas compartidas</Link> : "La sesión empieza con la primera entrada."}</p></CardHeader>
        <CardContent className="space-y-4"><div className="min-h-64 space-y-3" aria-label="Mensajes del teléfono">{state.data.messages.map(message => <div key={message.id} className={`max-w-[90%] rounded-xl px-4 py-3 ${message.direction === "in" ? "ml-auto bg-primary/10" : "bg-muted"}`}>
          <p className="mb-1 text-[11px] text-muted-foreground">{message.direction === "in" ? "Candidato" : "Agente"} · {message.channel}{message.simulated ? " · Simulado" : ""}{message.delivery_status ? ` · ${message.delivery_status}` : ""}</p><p className="whitespace-pre-wrap text-sm">{message.text}</p>
        </div>)}</div><form className="space-y-3" onSubmit={e => { e.preventDefault(); action.mutate("message"); }}>
          <Label>Mensaje del candidato<Textarea aria-label="Mensaje del teléfono" maxLength={2000} required value={text} onChange={e => setText(e.target.value)} placeholder="Escribe como candidato. BAJA o STOP cancelan el contacto." /></Label>
          <Button disabled={!text.trim() || action.isPending || state.data.opted_out} type="submit">{action.isPending ? "Procesando…" : "Enviar entrada simulada"}</Button>
        </form></CardContent>
      </Card>
      <Card className="h-fit"><CardHeader><CardTitle>Ventana y entrega</CardTitle></CardHeader><CardContent className="space-y-5">
        <p className="text-sm">Reloj de este teléfono: <span className="block font-medium">{new Date(state.data.now).toLocaleString("es-ES", { timeZoneName: "short" })}</span></p>
        <Button variant="outline" disabled={action.isPending} onClick={() => action.mutate("advance")}>Avanzar 25 horas</Button>
        <div className="flex flex-wrap gap-2"><Button variant="outline" disabled={action.isPending} onClick={() => action.mutate("advance2")}>Avanzar 2 horas</Button><Button variant="outline" disabled={action.isPending || !state.data.session_id || state.data.opted_out} onClick={() => action.mutate("schedule")}>Proponer recordatorio en 2 h</Button></div>
        <Link className="block text-sm underline" to={`/t/${tenantId}/reenganche`}>Ver y aprobar recordatorios</Link>
        <Label className="flex gap-2 items-start"><Checkbox checked={template} onCheckedChange={value => setTemplate(value === true)} />Usar plantilla de prueba si es necesaria</Label>
        <p className="text-xs text-muted-foreground">La aprobación es simulada. No implica que Meta haya aprobado esta plantilla.</p>
        <Button variant="secondary" disabled={action.isPending || state.data.opted_out} onClick={() => action.mutate("outbound")}>Probar mensaje proactivo</Button>
        <div className="space-y-2 text-xs">{state.data.events.slice(-5).map((event, index) => <pre key={index} className="overflow-auto rounded bg-muted p-2">{JSON.stringify(event.payload, null, 2)}</pre>)}</div>
      </CardContent></Card>
    </div>}
  </div>;
}
