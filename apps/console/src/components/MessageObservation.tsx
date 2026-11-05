import { useQuery } from "@tanstack/react-query";
import { api, type components } from "@screening/api-client";
import { languageLabel, moodLabel } from "@/lib/observations";

type Message = components["schemas"]["CandidateMessageOut"];
export type Observation = Pick<Message, "language" | "signal">;

/** Per-message language and mood, read from the scoped candidate endpoint. */
export function useMessageObservations(tenantId: string, sessionId: string, messages: number, live: boolean) {
  const query = useQuery({
    queryKey: ["message-observations", tenantId, sessionId, messages],
    enabled: !!sessionId && messages > 0,
    // Signals are recorded just after each candidate message; keep polling while the conversation is open.
    refetchInterval: live ? 5000 : false,
    queryFn: async () => {
      const r = await api.GET("/api/v1/tenants/{tenant_id}/candidates/{session_id}", { params: { path: { tenant_id: tenantId, session_id: sessionId } } });
      if (!r.data) throw new Error("No se pueden cargar las observaciones.");
      return new Map(r.data.messages.filter(m => m.id).map(m => [m.id!, { language: m.language, signal: m.signal }] as const));
    },
  });
  return query.data ?? new Map<string, Observation>();
}

export function MessageObservation({ value }: { value: Observation | undefined }) {
  const mood = value?.signal?.mood;
  if (!value?.language && !mood) return null;
  return <div className="message-observation" title="Observación para adaptar la conversación; no es un criterio de selección">
    <span className="sr-only">Observación, no es criterio de selección: </span>
    {value.language && <span>Idioma · {languageLabel(value.language)}{value.signal?.code_switching ? " (mezcla)" : ""}</span>}
    {mood && <span>Ánimo observado · {moodLabel(mood)}</span>}
  </div>;
}
