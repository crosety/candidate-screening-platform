import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "@screening/api-client";
import { EmptyState, PageHeader } from "@/components/WorkspaceUI";
import { InterviewActions, InterviewStatus, reminderLabel, type Interview } from "@/components/InterviewActions";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Card, CardContent } from "@/components/ui/card";

type StatusFilter = "" | Interview["status"];

function dayKey(interview: Interview): string {
  const text = new Intl.DateTimeFormat("es-ES", { timeZone: interview.site_timezone, weekday: "long", day: "numeric", month: "long" }).format(new Date(interview.starts_at));
  // "lunes, 2 de noviembre" → "Lunes, 2 de noviembre": only the first letter, as Spanish writes it.
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function timeText(interview: Interview): string {
  return new Intl.DateTimeFormat("es-ES", { timeZone: interview.site_timezone, hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(new Date(interview.starts_at));
}

export function AgendaPage() {
  const { tenantId } = useTenant();
  const [status, setStatus] = useState<StatusFilter>("");
  const [past, setPast] = useState(false);
  const query = useQuery({ queryKey: ["interviews", tenantId, status, past], refetchInterval: 30000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/interviews", { params: { path: { tenant_id: tenantId }, query: { status, include_past: past } } });
    if (!result.data) throw new Error(apiError(result.error, "No se ha podido cargar la agenda."));
    return result.data;
  }});
  const sites = new Map<string, Interview[]>();
  for (const interview of query.data ?? []) sites.set(interview.site_name, [...(sites.get(interview.site_name) ?? []), interview]);
  return <div className="space-y-6">
    <PageHeader eyebrow="Selección" title="Agenda" description="Entrevistas de 15 minutos con la persona encargada de cada local. Los cambios liberan el hueco, actualizan el ATS y avisan en la conversación de la persona candidata." />
    <div className="candidate-filters">
      <label className="select-field"><select aria-label="Filtrar por estado" value={status} onChange={e => setStatus(e.target.value as StatusFilter)}><option value="">Todas</option><option value="booked">Reservadas</option><option value="rescheduled">Cambiadas</option><option value="cancelled">Canceladas</option></select></label>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={past} onChange={e => setPast(e.target.checked)} />Incluir pasadas</label>
    </div>
    {query.error && <Alert variant="destructive"><AlertDescription>{query.error.message}</AlertDescription></Alert>}
    {query.isPending && <p className="table-loading" role="status">Cargando agenda…</p>}
    {query.data?.length === 0 && <EmptyState title="No hay entrevistas" description="Aparecerán aquí cuando el asistente reserve una entrevista con una persona candidata." />}
    {[...sites.entries()].map(([site, rows]) => <Card key={site}><CardContent className="space-y-4">
      <h2 className="text-base font-semibold">{site}</h2>
      {[...new Set(rows.map(dayKey))].map(day => <section key={day} aria-label={`${site}, ${day}`} className="space-y-2">
        <h3 className="text-sm font-medium text-muted-foreground">{day}</h3>
        <ul className="space-y-3">{rows.filter(r => dayKey(r) === day).map(interview => <li key={interview.id} className="rounded-lg border p-3 space-y-2">
          <div className="flex flex-wrap items-center gap-2"><strong>{timeText(interview)}</strong><Link className="underline" to={`/t/${tenantId}/candidaturas/${interview.session_id}`}>{interview.candidate_name}</Link><InterviewStatus interview={interview} /></div>
          <p className="text-xs text-muted-foreground">{interview.duration_minutes} min{interview.site_address ? ` · ${interview.site_address}` : ""} · {reminderLabel(interview)}{interview.candidate_timezone !== interview.site_timezone ? ` · Candidata: ${interview.candidate_time}` : ""}</p>
          <InterviewActions tenantId={tenantId} interview={interview} />
        </li>)}</ul>
      </section>)}
    </CardContent></Card>)}
  </div>;
}
