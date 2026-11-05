import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Tabs } from "radix-ui";
import { api, type components } from "@screening/api-client";
import { ArrowDown, ArrowLeft, Check, ClipboardCheck, Maximize2, MessageSquare, Minimize2, Phone, RefreshCw, UserRound } from "lucide-react";
import { useTenant } from "@/layout/AppLayout";
import { Avatar, StatusBadge } from "@/components/WorkspaceUI";
import { HumanConversation } from "@/components/HumanConversation";
import { SmsInvitation } from "@/components/SmsInvitation";
import { SessionInterview } from "@/components/InterviewActions";
import { MessageObservation, useMessageObservations } from "@/components/MessageObservation";
import { Button } from "@/components/ui/button";
import { apiError } from "@/lib/errors";
import { moment } from "./WorkspacePage";

type Decision = components["schemas"]["WorkspaceDecision"];
const decisionLabel: Record<string, string> = { qualify: "Apta", reject: "No apta", review: "Revisión humana", pool: "Bolsa de candidatos" };
const deliveryLabel: Record<string, string> = {
  pending: "En cola · entrega pendiente", dispatching: "Enviando · pendiente de confirmación",
  sent: "Enviado a WhatsApp", delivered: "Entregado", read: "Leído",
  blocked: "No enviado · requiere revisión", failed: "Entrega fallida · requiere revisión",
  unknown: "Entrega sin confirmar · revisar antes de reenviar",
};
const requirements: Record<string, string> = {
  pending: "Requisitos pendientes de comprobar",
  met: "Requisitos imprescindibles cumplidos",
  not_met: "Hay un requisito imprescindible no cumplido",
  not_configured: "Sin requisitos imprescindibles configurados",
};

function DecisionAction({ item }: { item: Decision }) {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [reason, setReason] = useState("");
  const mutation = useMutation({ mutationFn: async (choice: "qualify" | "reject") => {
    const r = await api.POST("/api/v1/tenants/{tenant_id}/decisions/{decision_id}", { params: { path: { tenant_id: tenantId, decision_id: item.id } }, body: { action: choice, reason } });
    if (!r.data) throw new Error(apiError(r.error));
    return r.data;
  }, onSuccess: () => void cache.invalidateQueries() });
  return <section className="candidate-panel decision-panel">
    <p className="eyebrow">{item.status === "proposed" ? "Pendiente del equipo" : "Decisión registrada"}</p>
    <h2>{item.status === "proposed" ? "Propuesta" : "Resultado"}: {decisionLabel[item.decision_type] ?? item.decision_type}</h2>
    <p>{item.reason}</p>
    {item.status === "proposed" ? <>
      <label className="form-label" htmlFor={`reason-${item.id}`}>Motivo de la decisión</label>
      <textarea id={`reason-${item.id}`} value={reason} onChange={e => setReason(e.target.value)} placeholder="Explica tu decisión; quedará registrada." maxLength={2000} />
      <div className="flex flex-wrap gap-2">
        <Button disabled={mutation.isPending || !reason.trim()} onClick={() => mutation.mutate("qualify")}>Marcar apta</Button>
        <Button variant="outline" disabled={mutation.isPending || !reason.trim()} onClick={() => mutation.mutate("reject")}>Descartar</Button>
      </div>
      <p className="evidence-date">Confirmar puede actualizar la candidatura en el sistema conectado.</p>
      {mutation.error && <p role="alert" className="inline-error">{mutation.error.message}</p>}
    </> : <p className="recorded-decision"><Check size={14} />{item.status === "applied" ? "Decisión aplicada" : "Propuesta resuelta"}</p>}
  </section>;
}

export function CandidatePage() {
  const { sessionId = "" } = useParams();
  // Reset local drafts and tab choice when switching to another candidate.
  return <CandidateDetail key={sessionId} sessionId={sessionId} />;
}

function CandidateDetail({ sessionId }: { sessionId: string }) {
  const { tenantId, tenant } = useTenant();
  const cache = useQueryClient();
  const [selectedTab, setSelectedTab] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);
  const dialogue = useRef<HTMLDialogElement>(null);
  const expandButton = useRef<HTMLButtonElement>(null);
  const transcript = useRef<HTMLDivElement>(null);
  const reply = useRef<HTMLDivElement>(null);
  const evaluationTab = useRef<HTMLButtonElement>(null);
  const following = useRef(true);
  const scrollPosition = useRef(0);
  const query = useQuery({ queryKey: ["candidate-workspace", tenantId, sessionId], refetchInterval: 3000, queryFn: async () => {
    const r = await api.GET("/api/v1/tenants/{tenant_id}/workspace/{session_id}", { params: { path: { tenant_id: tenantId, session_id: sessionId } } });
    if (!r.data) throw new Error(apiError(r.error));
    return r.data;
  }});
  const snapshot = useMutation({ mutationFn: async () => {
    const r = await api.POST("/api/v1/tenants/{tenant_id}/workspace/{session_id}/snapshot", { params: { path: { tenant_id: tenantId, session_id: sessionId } } });
    if (!r.data) throw new Error(apiError(r.error));
    return r.data;
  }, onSuccess: () => void cache.invalidateQueries({ queryKey: ["candidate-workspace", tenantId, sessionId] }) });
  const data = query.data;
  const observations = useMessageObservations(tenantId, sessionId, data?.messages.length ?? 0, ["active", "handed_off"].includes(data?.status ?? ""));
  const tab = selectedTab ?? (data && ["completed", "abandoned"].includes(data.status) ? "evaluation" : "conversation");
  useEffect(() => {
    if (!expanded) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, [expanded]);
  function expandConversation(value: boolean) {
    const el = dialogue.current;
    if (!el) return;
    // Promote the same panel to the top layer: keep transcript and reply mounted.
    el.close();
    if (value) el.showModal(); else el.show();
    setExpanded(value);
    requestAnimationFrame(() => expandButton.current?.focus({ preventScroll: true }));
  }
  // Background updates must not move the user to another tab.
  useEffect(() => { if (data && selectedTab === null) setSelectedTab(tab); }, [data, selectedTab, tab]);
  useEffect(() => {
    if (tab === "conversation" && transcript.current) {
      transcript.current.scrollTop = following.current ? transcript.current.scrollHeight : scrollPosition.current;
    }
  }, [tab, data?.messages.length]);
  useEffect(() => {
    const el = transcript.current;
    if (!el) return;
    // The reply box loads separately and can resize; keep the latest message
    // visible only while following, never jump away from an older message.
    const observer = new ResizeObserver(() => {
      if (following.current && el.clientHeight > 0) el.scrollTop = el.scrollHeight;
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [!!data]);
  function changeTab(value: string) {
    if (tab === "conversation" && transcript.current) scrollPosition.current = transcript.current.scrollTop;
    setSelectedTab(value);
  }
  function attend() {
    changeTab("conversation");
    requestAnimationFrame(() => {
      reply.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      reply.current?.querySelector<HTMLElement>("textarea,button:not(:disabled)")?.focus({ preventScroll: true });
    });
  }
  function reviewDecision() {
    changeTab("evaluation");
    requestAnimationFrame(() => evaluationTab.current?.focus());
  }
  if (query.error && !data) return <div role="alert" className="inline-error">{query.error.message}<Button onClick={() => void query.refetch()}>Reintentar</Button></div>;
  if (!data) return <p role="status">Abriendo candidatura…</p>;
  const phone = data.phone.replace(/[^+\d]/g, "");
  const busySnapshot = snapshot.isPending || ["queued", "running"].includes(data.snapshot_status);
  const open = ["active", "handed_off"].includes(data.status);
  const pendingDecisions = data.decisions.filter(item => item.status === "proposed").length;
  const values = data.fields as Record<string, { value?: unknown; evidence?: string; confidence?: string } | undefined>;
  const missing = data.field_definitions.filter(field => field.required && values[field.key]?.value == null).length;
  return <div className="candidate-workspace candidate-detail">
    <Link className="workspace-back" to={`/t/${tenantId}/${data.is_simulation && tenant.role !== "recruiter" ? "simulacion?tab=conversaciones" : "bandeja"}`}>
      <ArrowLeft size={15} />{data.is_simulation && tenant.role !== "recruiter" ? "Volver a simulación" : "Volver a la bandeja"}
    </Link>
    <header className="candidate-workspace-header">
      <Avatar name={data.name} />
      <div className="candidate-identity">
        <p className="eyebrow">{data.offer_title}</p>
        <div className="candidate-title"><h1>{data.name}</h1><StatusBadge status={data.status} /></div>
        <div className="candidate-contact">
          {phone ? <a href={`tel:${phone}`} aria-label={`Llamar al ${data.phone}`} title="Abrir el teléfono de este dispositivo"><Phone size={14} />{data.phone}</a> : <span>Teléfono no facilitado</span>}
          <span><UserRound size={14} />{data.owner ?? "Sin responsable asignado"}</span>
        </div>
      </div>
      <div className="candidate-header-actions">
        {open && <Button onClick={attend}><MessageSquare size={14} />Atender conversación</Button>}
        {!open && pendingDecisions > 0 && tab !== "evaluation" && <Button onClick={reviewDecision}><ClipboardCheck size={14} />Revisar decisión</Button>}
      </div>
    </header>
    <div className="candidate-key-dates">
      <span>Recibida <time dateTime={data.received_at}>{moment(data.received_at)}</time></span>
      <span>Última interacción <time dateTime={data.last_interaction_at ?? undefined}>{moment(data.last_interaction_at)}</time></span>
    </div>
    {data.is_simulation && <div className="simulation-notice"><span>Simulación</span> Esta conversación no cuenta en la actividad de selección.</div>}
    <SmsInvitation tenantId={tenantId} sessionId={sessionId} phone={phone} />
    <SessionInterview tenantId={tenantId} sessionId={sessionId} />
    {query.error && <p className="inline-error" role="alert">No se ha podido actualizar. Los datos mostrados pueden haber cambiado.</p>}
    {data.status === "handed_off" && <div className="handoff-banner">
      <MessageSquare size={18} aria-hidden="true" />
      <div><strong>{data.owner ? "El equipo está atendiendo esta conversación" : "Necesita atención humana"}</strong>
        <p>{data.handoff_reason === "keyword" ? "Ha solicitado hablar con una persona." : data.handoff_reason || "El asistente ha derivado la conversación."}</p>
      </div>
      <span>IA en pausa</span>
    </div>}
    <Tabs.Root value={tab} onValueChange={changeTab} className="candidate-tabs">
      <Tabs.List className="candidate-tab-list" aria-label="Detalle de candidatura">
        <Tabs.Trigger value="conversation"><MessageSquare size={16} aria-hidden="true" />Conversación<span className="tab-count">{data.messages.length}</span></Tabs.Trigger>
        <Tabs.Trigger value="profile"><UserRound size={16} aria-hidden="true" />Perfil</Tabs.Trigger>
        <Tabs.Trigger ref={evaluationTab} value="evaluation"><ClipboardCheck size={16} aria-hidden="true" />Evaluación{pendingDecisions > 0 && <span className="tab-attention" aria-label="Decisión pendiente" />}</Tabs.Trigger>
      </Tabs.List>

      {/* Keep mounted so switching tabs cannot discard reply or decision drafts. */}
      <Tabs.Content value="conversation" forceMount hidden={tab !== "conversation"} className="candidate-tab-panel">
        <dialog open ref={dialogue} className="candidate-dialogue surface" role={expanded ? "dialog" : "region"} aria-modal={expanded || undefined} aria-label={`Conversación con ${data.name}`} onCancel={e => { e.preventDefault(); expandConversation(false); }}>
          <div className="dialogue-toolbar">
            {expanded ? <div className="dialogue-context"><strong>{data.name}</strong><span>{data.offer_title}{phone && <> · <a href={`tel:${phone}`}>{data.phone}</a></>}</span></div> : <span>{open ? "Historial y atención al candidato" : "Historial de la conversación"}</span>}
            <div className="dialogue-tools">
            <Button variant="ghost" size="sm" onClick={() => { following.current = true; transcript.current?.scrollTo({ top: transcript.current.scrollHeight, behavior: "smooth" }); }}><ArrowDown size={14} />Último mensaje</Button>
            <Button ref={expandButton} variant="outline" size="sm" onClick={() => expandConversation(!expanded)} aria-label={expanded ? "Salir de pantalla completa" : "Pantalla completa"}>{expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}<span>{expanded ? "Salir" : "Pantalla completa"}</span></Button>
            </div>
          </div>
          <div className="hr-transcript" ref={transcript} onScroll={e => {
            const el = e.currentTarget;
            if (tab === "conversation") { following.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; scrollPosition.current = el.scrollTop; }
          }} role="log" tabIndex={0} aria-label="Historial de conversación" aria-live={tab === "conversation" ? "polite" : "off"} aria-relevant="additions">
            <p className="transcript-start">Inicio · {moment(data.started_at)}</p>
            {data.messages.length === 0 && <p className="transcript-start">Aún no hay mensajes.</p>}
            {data.messages.map(m => <article key={m.id} className={`hr-message ${m.direction === "in" ? "from-candidate" : m.channel === "human" ? "from-human" : "from-assistant"}`}>
              <header><strong>{m.direction === "in" ? data.name : m.channel === "human" ? "Equipo de selección" : "Asistente"}</strong><span>{m.channel === "voice" ? "Voz · " : ""}<time dateTime={m.created_at}>{moment(m.created_at)}</time></span></header><p>{m.text}</p>
              {m.direction === "in" && <MessageObservation value={observations.get(m.id)} />}
              {m.direction === "out" && m.delivery_status && <small>{deliveryLabel[m.delivery_status] ?? "Estado de entrega pendiente de comprobar"}</small>}
            </article>)}
          </div>
          <div id="human-reply" ref={reply}><HumanConversation tenantId={tenantId} sessionId={sessionId} /></div>
        </dialog>
      </Tabs.Content>

      <Tabs.Content value="profile" forceMount hidden={tab !== "profile"} className="candidate-tab-panel">
        <section className="candidate-panel">
          <div className="section-intro"><div><h2>Información del candidato</h2><p>Datos recogidos durante la entrevista, con sus respuestas originales.</p></div>{data.field_definitions.length > 0 && <span className="detail-tag">{missing ? `${missing} por confirmar` : "Datos necesarios completos"}</span>}</div>
          <dl className="candidate-facts">
            {data.field_definitions.map(field => { const value = values[field.key]; return <div key={field.key}>
              <dt>{field.label || field.description.split(/[:;]/)[0]?.replace(/ ISO.*$/, "")}{field.required && <span>Necesario</span>}</dt>
              <dd>{value?.value == null ? <span className="fact-pending">Sin confirmar</span> : typeof value.value === "boolean" ? (value.value ? "Sí" : "No") : String(value.value)}
                {value?.evidence && <details><summary>Ver respuesta del candidato</summary><blockquote>«{value.evidence}»</blockquote></details>}
              </dd>
            </div>; })}
          </dl>
          {data.field_definitions.length === 0 && <p>Todavía no hay datos estructurados para esta candidatura. Puedes consultar la conversación.</p>}
        </section>
        <section className="candidate-panel candidate-timeline"><h2>Fechas de la candidatura</h2><dl>
          {[["Candidatura recibida", data.received_at], ["Inicio de conversación", data.started_at], ["Última interacción", data.last_interaction_at], ["Derivada al equipo", data.handed_off_at]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd><time dateTime={value ?? undefined}>{value ? moment(value) : "No registrada"}</time></dd></div>)}
        </dl></section>
        <details className="candidate-panel offer-context"><summary>Sobre la oferta · {data.offer_title}</summary><p className="whitespace-pre-wrap">{data.offer_description}</p>
          {tenant.role !== "recruiter" && <><p className="evidence-date">Configuración conservada: {data.version}</p><Link className="text-sm underline" to={`/t/${tenantId}/conversaciones/${sessionId}`}>Abrir diagnóstico técnico</Link></>}
        </details>
      </Tabs.Content>

      <Tabs.Content value="evaluation" forceMount hidden={tab !== "evaluation"} className="candidate-tab-panel">
        <section className="candidate-panel assessment-summary">
          <div className="assessment-score"><span>Encaje con la oferta</span><strong>{data.fit.score ?? "—"}<small>{data.fit.score !== null ? "/ 100" : "Sin puntuación"}</small></strong><span>{data.fit.coverage}% de criterios con evidencia</span></div>
          <div className="assessment-narrative">
            <div className="evidence-heading"><div><p className="eyebrow">{data.provisional ? "Evaluación en curso" : "Evaluación de la entrevista"}</p><h2>{data.provisional ? "Valoración provisional" : "Valoración del candidato"}</h2></div>
              {data.provisional && <Button variant="outline" size="sm" disabled={busySnapshot || !data.messages.some(m => m.direction === "in")} onClick={() => snapshot.mutate()}><RefreshCw size={14} className={busySnapshot ? "animate-spin" : ""} />{busySnapshot ? "Actualizando…" : "Actualizar resumen"}</Button>}
            </div>
            <p>{data.summary || (busySnapshot ? "Preparando el resumen de las respuestas…" : "Todavía no hay una valoración. Puedes actualizarla sin cerrar la entrevista ni tomar una decisión.")}</p>
            {data.snapshot_at && <p className="evidence-date">Actualizado {moment(data.snapshot_at)}{data.snapshot_stale ? " · Hay mensajes posteriores" : ""}</p>}
            {data.provisional && <p className="evidence-date">Provisional: la conversación puede aportar información nueva.</p>}
            {["dead", "failed"].includes(data.snapshot_status) && <p role="alert" className="inline-error">No se pudo preparar el resumen. La conversación y la atención humana siguen disponibles.</p>}
            {snapshot.error && <p role="alert" className="inline-error">{snapshot.error.message}</p>}
          </div>
        </section>
        <section className="candidate-panel criteria-panel">
          <div className="section-intro"><div><h2>Criterios de la oferta</h2><p>{data.fit.items.length ? requirements[data.fit.requirements] : "Esta versión no tiene criterios de puntuación. No se ha estimado una cifra."}</p></div></div>
          {data.fit.items.map(item => <details className="fit-criterion" key={item.key}><summary><span>{item.label}<small>{item.required ? "Imprescindible" : "Preferencia"} · peso {item.weight}</small></span><span className={`criterion-${item.state}`}>{item.state === "met" ? "Cumple" : item.state === "not_met" ? "No cumple" : "Pendiente"}</span></summary><p>{item.evidence ? `«${item.evidence}»` : "No hay evidencia suficiente."}</p></details>)}
          <p className="evidence-date">El encaje se calcula con criterios y evidencias. No es una probabilidad de éxito ni sustituye la decisión del equipo.</p>
        </section>
        {data.decisions.map(item => <DecisionAction key={item.id} item={item} />)}
        {data.decisions.length === 0 && <p className="candidate-panel-note">Todavía no hay una decisión propuesta. La evaluación no cambia el estado de esta candidatura.</p>}
      </Tabs.Content>
    </Tabs.Root>
  </div>;
}
