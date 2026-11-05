import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { Button } from "@/components/ui/button";
import { apiError } from "@/lib/errors";

const states: Record<string, string> = { queued: "En cola", sending: "Enviando", accepted: "Aceptado por el operador", sent: "Enviado; entrega pendiente", delivered: "Entregado", failed: "No enviado", undelivered: "No entregado", unknown: "Envío sin confirmar · no reenviar", canceled: "Cancelado" };

export function SmsInvitation({ tenantId, sessionId, phone: initialPhone }: { tenantId: string; sessionId: string; phone: string }) {
  const [phone, setPhone] = useState(initialPhone);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const key = useRef(crypto.randomUUID());
  const params = { path: { tenant_id: tenantId, session_id: sessionId } };
  const query = useQuery({ queryKey: ["sms-link", tenantId, sessionId], refetchInterval: 15000, queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/sessions/{session_id}/sms-link", { params });
    if (!r.data) throw new Error("No se pudo consultar el estado del SMS.");
    return r.data;
  }});
  async function send() {
    setBusy(true); setError("");
    try {
      const r = await api.POST("/api/v1/tenants/{tenant_id}/sessions/{session_id}/sms-link", { params, body: { request_id: key.current, phone, consent } });
      if (!r.data) throw new Error(apiError(r.error, "No se pudo confirmar el envío. No se repetirá automáticamente."));
      await query.refetch();
    } catch (e) { setError(e instanceof Error ? e.message : "No se pudo confirmar el envío."); }
    finally { setBusy(false); }
  }
  const sent = query.data?.notifications.some(n => n.purpose === "invitation" && !n.revoked);
  return <><details className="candidate-panel"><summary>Invitación por SMS</summary><div className="space-y-3 mt-4">
    <p>Envía el enlace personal de esta candidatura. Desde él podrá escribir, hablar por el navegador o solicitar una llamada. Tras una llamada atendida recibirá un enlace para continuar.</p>
    {query.isPending && <p role="status">Consultando los SMS…</p>}
    {query.data && !query.data.available && <p role="status">El envío de SMS no está disponible ahora.</p>}
    {query.data?.notifications.map(n => <p key={n.id}>{n.purpose === "after_call" ? "Tras la llamada" : "Invitación"}: {states[n.state] || n.state}{n.revoked ? " · Avisos desactivados" : ""}</p>)}
    {!sent && query.data?.available && !query.error && <><label className="form-label">Móvil invitado<input type="tel" value={phone} onChange={e => setPhone(e.target.value)} placeholder="+34 600000000" /></label><label className="flex gap-2"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /><span>El candidato ha solicitado recibir este enlace y el aviso posterior a la llamada en este número.</span></label><Button disabled={busy || !consent || !/^\+34[67]\d{8}$/.test(phone.trim())} onClick={() => void send()}>{busy ? "Solicitando…" : "Enviar enlace por SMS"}</Button></>}
    {error && <p role="alert" className="inline-error">{error}</p>}
  </div></details>{query.error && <div role="alert"><p>No se pudo consultar el estado del SMS. No reenvíes el SMS sin comprobarlo.</p><Button variant="outline" disabled={query.isFetching} onClick={() => void query.refetch()}>Reintentar SMS</Button></div>}</>;
}
