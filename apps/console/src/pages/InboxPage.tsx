import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, type components } from "@screening/api-client";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { ResultFields } from "@/components/ResultFields";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { CheckCheck, Clock3, ShieldCheck } from "lucide-react";
import { Avatar, EmptyState, PageHeader } from "@/components/WorkspaceUI";

const labels: Record<string, string> = { qualify: "Apto", reject: "No apto", review: "Revisar", pool: "Bolsa" };
type Item = components["schemas"]["InboxOut"];

function InboxCard({ item }: { item: Item }) {
  const { tenantId, tenant } = useTenant();
  const cache = useQueryClient();
  const [reason, setReason] = useState("");
  const action = useMutation({ mutationFn: async (choice: "confirm" | "qualify" | "reject") => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/decisions/{decision_id}", {
      params: { path: { tenant_id: tenantId, decision_id: item.decision_id } }, body: { action: choice, reason },
    });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: () => void cache.invalidateQueries({ queryKey: ["inbox", tenantId] }) });
  const flags: Record<string, string> = { unclear_requirement: "Hay un requisito que necesita aclaración", prompt_injection_attempt: "Se ha detectado un intento de alterar las instrucciones", third_party_data_request: "Se ha solicitado información de otra candidatura", sensitive_data_shared: "La conversación contiene información sensible" };
  return <Card className="inbox-card"><CardHeader className="inbox-card-header"><div className="flex items-center gap-3"><Avatar name={item.candidate_name} /><div><CardTitle>{item.candidate_name}</CardTitle><p className="mt-1 flex items-center gap-1.5 text-[10px] text-muted-foreground"><Clock3 size={11} />{new Date(item.created_at).toLocaleString("es")}</p></div></div><Badge variant="secondary">{labels[item.decision_type]}</Badge></CardHeader><CardContent className="space-y-5"><div className="decision-summary"><p className="eyebrow">Por qué necesita tu revisión</p><p className="mt-2 leading-relaxed text-sm">{item.reason}</p><p className="mt-2 text-xs leading-relaxed text-muted-foreground">{item.summary}</p></div><details className="surface disclosure-panel" open><summary><ShieldCheck size={14} />Evidencias de la evaluación</summary><div className="px-4 pb-3"><ResultFields fields={item.fields} /></div></details>{item.flags.map(flag => <p key={flag} className="review-notice"><ShieldCheck size={14} />{flags[flag] ?? flag}</p>)}{tenant.role !== "recruiter" && <Link className="text-xs underline underline-offset-4" to={`/t/${tenantId}/conversaciones/${item.session_id}`}>Ver conversación completa</Link>}<div className="decision-actions"><label className="block text-xs font-medium mb-2" htmlFor={`reason-${item.decision_id}`}>Motivo de tu decisión</label><Textarea id={`reason-${item.decision_id}`} aria-label="Motivo" placeholder="Añade contexto. Es obligatorio si cambias la propuesta." value={reason} onChange={e => setReason(e.target.value)} />{action.error && <p role="alert" className="inline-error">{action.error.message}</p>}<div className="mt-4 flex flex-wrap items-center gap-2">{["qualify", "reject"].includes(item.decision_type) && <Button disabled={action.isPending} onClick={() => action.mutate("confirm")}>Confirmar</Button>}<Button variant="outline" disabled={action.isPending || (item.decision_type !== "qualify" && !reason.trim())} onClick={() => action.mutate("qualify")}><CheckCheck size={14} />Marcar apta</Button><Button variant="ghost" disabled={action.isPending || (item.decision_type !== "reject" && !reason.trim())} onClick={() => action.mutate("reject")}>Descartar</Button><span className="ml-auto text-[10px] text-muted-foreground">La decisión quedará registrada.</span></div></div></CardContent></Card>;
}

export function InboxPage() {
  const { tenantId } = useTenant();
  const items = useQuery({ queryKey: ["inbox", tenantId], refetchInterval: 10000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/inbox", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  return <div className="space-y-6"><PageHeader eyebrow="Revisión humana" title="Bandeja" description="Aquí entran las decisiones que necesitan tu criterio antes de continuar." actions={<span className="scope-label"><ShieldCheck size={14} /> Decisiones con trazabilidad</span>} />{items.data && <div className="inbox-count"><span className="count-pill">{items.data.length}</span><span>{items.data.length === 1 ? "decisión pendiente de revisión" : "decisiones pendientes de revisión"}</span><span className="inbox-count-note">Actualización automática</span></div>}{items.error && <p role="alert" className="inline-error">{items.error.message}</p>}{items.isPending && <p role="status" className="table-loading">Cargando decisiones…</p>}{items.data?.map(item => <InboxCard key={item.decision_id} item={item} />)}{items.data?.length === 0 && <div className="surface"><EmptyState title="Todo al día" description="No hay decisiones pendientes. Las candidaturas que necesiten revisión aparecerán aquí." /></div>}</div>;
}
