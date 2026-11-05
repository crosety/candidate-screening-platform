import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { Button } from "@/components/ui/button";

export function HumanConversation({ tenantId, sessionId }: { tenantId: string; sessionId: string }) {
  const client = useQueryClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pendingMessage = useRef<{ text: string; id: string } | null>(null);
  const params = { path: { tenant_id: tenantId, session_id: sessionId } };
  const state = useQuery({ queryKey: ["human", tenantId, sessionId], refetchInterval: 2000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/sessions/{session_id}/human", { params });
    if (!result.data) throw new Error("No se puede comprobar quién atiende la conversación.");
    return result.data;
  }});
  async function act(action: "claim" | "message" | "resume_ai" | "close") {
    setBusy(true); setError("");
    try {
      if (action === "message" && pendingMessage.current?.text !== text) pendingMessage.current = { text, id: crypto.randomUUID() };
      const result = await api.POST("/api/v1/tenants/{tenant_id}/sessions/{session_id}/human", { params, body: { action, text, message_id: action === "message" ? pendingMessage.current!.id : null } });
      if (!result.data) throw new Error("No se pudo realizar la acción. Comprueba si otro recruiter ha tomado la conversación.");
      if (action === "message") { setText(""); pendingMessage.current = null; }
      await client.invalidateQueries();
    } catch (e) { setError(e instanceof Error ? e.message : "No se pudo realizar la acción."); }
    finally { setBusy(false); }
  }
  if (!state.data && state.isError) return <div role="alert"><p>No se pudo comprobar la atención humana.</p><Button variant="outline" onClick={() => void state.refetch()}>Reintentar</Button></div>;
  if (!state.data) return <p role="status">Comprobando atención humana…</p>;
  if (!["active", "handed_off"].includes(state.data.status)) return <p className="text-sm text-muted-foreground">Conversación cerrada.</p>;
  return <section className="rounded-xl border p-4 space-y-3 mt-4" aria-label="Atención humana" data-owned={state.data.owned_by_me}><div><h3 className="font-semibold">Atención humana</h3><p className="text-sm text-muted-foreground">{state.data.owned_by_me ? "Estás atendiendo a esta persona. La IA está pausada." : state.data.owner_id ? "Otro miembro del equipo está atendiendo esta conversación." : "Toma la conversación para responder directamente. El candidato podrá seguir escribiendo."}</p></div>
    {error && <p role="alert" className="text-sm text-red-700">{error}</p>}
    {state.data.owned_by_me ? <><textarea aria-label="Mensaje de HR" className="w-full rounded-lg border p-3 text-sm min-h-24" value={text} maxLength={2000} onChange={e => setText(e.target.value)} placeholder="Escribe al candidato como miembro del equipo…" /><div className="flex flex-wrap gap-2"><Button disabled={busy || !text.trim()} onClick={() => void act("message")}>Enviar mensaje al candidato</Button><Button variant="outline" disabled={busy} onClick={() => void act("resume_ai")}>Devolver a la IA</Button><Button variant="outline" disabled={busy} onClick={() => void act("close")}>Cerrar conversación</Button></div></> : <Button disabled={busy || !!state.data.owner_id} onClick={() => void act("claim")}>Tomar conversación</Button>}
  </section>;
}
