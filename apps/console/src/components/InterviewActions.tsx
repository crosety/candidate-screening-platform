import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CalendarX2, Download } from "lucide-react";
import { api, type components } from "@screening/api-client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiError } from "@/lib/errors";

export type Interview = components["schemas"]["InterviewOut"];

export const interviewLabels: Record<Interview["status"], string> = { booked: "Reservada", rescheduled: "Cambiada", cancelled: "Cancelada" };
const reminderLabels: Record<string, string> = { scheduled: "Recordatorio programado", sent: "Recordatorio enviado", none: "Sin recordatorio (falta poco)", not_scheduled: "Sin recordatorio programado", cancelled: "Recordatorio cancelado", skipped: "Recordatorio omitido por horario", expired: "Recordatorio no enviado a tiempo", unavailable: "Recordatorio sin canal" };

export function InterviewStatus({ interview }: { interview: Interview }) {
  return <Badge variant={interview.status === "cancelled" ? "secondary" : "outline"}>{interviewLabels[interview.status]}{interview.status === "cancelled" && interview.cancelled_by ? ` · ${interview.cancelled_by === "candidate" ? "por la persona candidata" : "por el equipo"}` : ""}</Badge>;
}

export function reminderLabel(interview: Interview): string {
  return reminderLabels[interview.reminder_status] ?? interview.reminder_status;
}

export function InterviewActions({ tenantId, interview, onChanged }: { tenantId: string; interview: Interview; onChanged?: () => void }) {
  const cache = useQueryClient();
  const [mode, setMode] = useState<"idle" | "reschedule" | "cancel">("idle");
  const [slot, setSlot] = useState("");
  const path = { tenant_id: tenantId, booking_id: interview.id };
  const slots = useQuery({ queryKey: ["interview-slots", tenantId, interview.id], enabled: mode === "reschedule", queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/interviews/{booking_id}/slots", { params: { path } });
    if (!result.data) throw new Error(apiError(result.error, "No se han podido cargar los huecos."));
    return result.data;
  }});
  const change = useMutation({ mutationFn: async (action: "cancel" | "reschedule") => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/interviews/{booking_id}", { params: { path }, body: { action, slot_id: action === "reschedule" ? slot : "" } });
    if (!result.data) throw new Error(apiError(result.error, "No se ha podido actualizar la agenda."));
    setMode("idle"); setSlot("");
    await cache.invalidateQueries({ queryKey: ["interviews", tenantId] });
    await cache.invalidateQueries({ queryKey: ["session-interview", tenantId] });
    onChanged?.();
  }});
  const active = interview.status !== "cancelled";
  const upcoming = new Date(interview.starts_at).getTime() > Date.now();
  return <div className="space-y-2">
    <div className="flex flex-wrap gap-2">
      <Button asChild size="sm" variant="ghost"><a href={`/api/v1/tenants/${tenantId}/interviews/${interview.id}/ics`} download="entrevista.ics"><Download size={14} aria-hidden="true" />Calendario (.ics)</a></Button>
      {active && upcoming && mode === "idle" && <><Button size="sm" variant="outline" onClick={() => setMode("reschedule")}><CalendarClock size={14} aria-hidden="true" />Cambiar</Button><Button size="sm" variant="outline" onClick={() => setMode("cancel")}><CalendarX2 size={14} aria-hidden="true" />Cancelar</Button></>}
    </div>
    {mode === "reschedule" && <div className="flex flex-wrap items-center gap-2">
      <label className="select-field"><select aria-label="Nuevo hueco" value={slot} onChange={e => setSlot(e.target.value)} disabled={slots.isPending}><option value="">{slots.isPending ? "Cargando huecos…" : slots.data?.length ? "Elige un hueco libre" : "No hay huecos libres"}</option>{slots.data?.map(s => <option key={s.slot_id} value={s.slot_id}>{s.starts_at_local}</option>)}</select></label>
      <Button size="sm" disabled={!slot || change.isPending} onClick={() => change.mutate("reschedule")}>Confirmar cambio</Button>
      <Button size="sm" variant="ghost" onClick={() => { setMode("idle"); setSlot(""); }}>Volver</Button>
    </div>}
    {mode === "cancel" && <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Confirmar cancelación">
      <span className="text-sm">Se liberará el hueco y se avisará en su conversación.</span>
      <Button size="sm" variant="destructive" disabled={change.isPending} onClick={() => change.mutate("cancel")}>Cancelar entrevista</Button>
      <Button size="sm" variant="ghost" onClick={() => setMode("idle")}>Volver</Button>
    </div>}
    {(change.error || slots.error) && <p className="inline-error" role="alert">{(change.error || slots.error)?.message}</p>}
  </div>;
}

export function SessionInterview({ tenantId, sessionId }: { tenantId: string; sessionId: string }) {
  const query = useQuery({ queryKey: ["session-interview", tenantId, sessionId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/sessions/{session_id}/interview", { params: { path: { tenant_id: tenantId, session_id: sessionId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data.interview;
  }});
  const interview = query.data;
  const notice = query.isError ? <div role="alert"><p>No se ha podido consultar la entrevista. Los datos anteriores pueden no estar actualizados.</p><Button size="sm" variant="outline" onClick={() => void query.refetch()}>Reintentar entrevista</Button></div>
    : query.isPending ? <p role="status">Consultando entrevista…</p> : null;
  if (!interview) return notice ? <section className="surface" aria-label="Entrevista">{notice}</section> : null;
  return <section className="handoff-banner" aria-label="Entrevista">
    <CalendarClock size={18} aria-hidden="true" />
    <div className="space-y-1"><strong>Entrevista · {interview.site_name}</strong> <InterviewStatus interview={interview} />
      {notice}
      <p>{interview.starts_at_local} · {interview.duration_minutes} min{interview.site_address ? ` · ${interview.site_address}` : ""}</p>
      {interview.candidate_timezone !== interview.site_timezone && <p className="text-xs text-muted-foreground">Para la persona candidata: {interview.candidate_time}</p>}
      <p className="text-xs text-muted-foreground">{reminderLabel(interview)}</p>
      <InterviewActions tenantId={tenantId} interview={interview} />
    </div>
  </section>;
}
