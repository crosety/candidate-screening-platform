import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { Link } from "react-router";
import { Cable, ChevronRight, FileText, Gauge, ListChecks, MessagesSquare, Network, Save, ShieldCheck, SlidersHorizontal } from "lucide-react";
import { PageHeader } from "@/components/WorkspaceUI";
import { SourcesEditor, RubricEditor } from "@/components/ProfileEditors";
import { CopilotSuggestions } from "@/components/CopilotSuggestions";

import { ConnectionsEditor } from "@/components/ConnectionsEditor";
import { ChannelSettingsEditor } from "@/components/ChannelSettingsEditor";

import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { autonomyLabels, autonomyLevels, briefSections, capabilities, type CapabilityId, type ProfileContent } from "@/lib/profile";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { ResultSchemaEditor } from "@/components/ResultSchemaEditor";
import { Label } from "@/components/ui/label";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

const companyTabs = [["brief", "Empresa y asistente"], ["operating", "Instrucciones comunes"], ["channels", "Canales"], ["connections", "Conexiones"], ["grants", "Permisos"], ["result", "Campos comunes"], ["autonomy", "Autonomía"], ["invariants", "Reglas de plataforma"]];
const tabIcons = [FileText, Network, MessagesSquare, Cable, SlidersHorizontal, ListChecks, Gauge, ShieldCheck];
const invariants = [
  ["Contrato del resultado", "Cada resultado sigue el esquema del cliente y conserva las evidencias y quién decidió."],
  ["Contextos de ejecución", "Cada agente actúa para un cliente, una persona y una candidatura concretos."],
  ["Capacidades, no herramientas", "El perfil concede capacidades. La plataforma controla qué conectores pueden ejecutarlas."],
  ["Aviso de IA", "El candidato sabe desde el principio que habla con un asistente virtual."],
  ["Datos personales", "El agente no comparte información de otros candidatos ni conserva direcciones completas."],
  ["Evaluador aislado", "El evaluador lee la conversación como datos y no tiene herramientas para actuar."],
  ["Auditoría", "Las versiones, llamadas, evidencias y decisiones conservan su trazabilidad."],
  ["Preguntas prohibidas", "No se pregunta edad, nacionalidad, origen, salud, discapacidad, embarazo, religión, orientación sexual ni afiliación política o sindical."],
];

export function ProfilePage({ mode = "company" }: { mode?: "company" | "library" | "quality" }) {
  const { tenantId } = useTenant();
  return <ProfileEditor key={`${tenantId}-${mode}`} mode={mode} />;
}

function ProfileEditor({ mode }: { mode: "company" | "library" | "quality" }) {
  const tabs = mode === "company" ? companyTabs : mode === "library" ? [["sources", "Documentos"]] : [["rubric", "Calidad del agente"]];
  const { tenantId, tenant } = useTenant();
  const cache = useQueryClient();
  const draft = useQuery({ queryKey: ["draft", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/draft", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const [local, setLocal] = useState<ProfileContent | null>(null);
  const [baseline, setBaseline] = useState<ProfileContent | null>(null);
  const [rev, setRev] = useState(0);
  const [section, setSection] = useState(mode === "company" ? "brief" : mode === "library" ? "sources" : "rubric");
  const dirty = local !== null && JSON.stringify(local) !== JSON.stringify(baseline);
  useUnsavedChanges(dirty);
  useEffect(() => { if (draft.data && !dirty) { setLocal(structuredClone(draft.data.content)); setBaseline(draft.data.content); setRev(draft.data.rev); } }, [draft.data, dirty]);
  function edit(change: (content: ProfileContent) => void) {
    setLocal(current => { if (!current) return current; const next = structuredClone(current); change(next); return next; });
  }
  const save = useMutation({ mutationFn: async () => {
    if (!local) throw new Error("No hay perfil cargado.");
    const result = await api.PUT("/api/v1/tenants/{tenant_id}/draft", { params: { path: { tenant_id: tenantId } }, body: { rev, content: local } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: data => {
    setLocal(structuredClone(data.content)); setBaseline(data.content); setRev(data.rev);
    cache.setQueryData(["draft", tenantId], data);
    void cache.invalidateQueries({ queryKey: ["tenants"] });
    void cache.invalidateQueries({ queryKey: ["diff", tenantId] });
    void cache.invalidateQueries({ queryKey: ["checks", tenantId] });
    void cache.invalidateQueries({ queryKey: ["gate", tenantId] });
    void cache.invalidateQueries({ queryKey: ["offers", tenantId] });
  }});
  if (draft.isError) return <Alert variant="destructive"><AlertDescription>{draft.error.message}</AlertDescription></Alert>;
  if (!local) return <p>Cargando perfil…</p>;
  async function discard() {
    save.reset();
    const result = await draft.refetch();
    if (result.data) { setLocal(structuredClone(result.data.content)); setBaseline(result.data.content); setRev(result.data.rev); }
  }
  return <fieldset disabled={save.isPending} className={`space-y-6 fde-page min-w-0 profile-mode-${mode}`}>
    <PageHeader eyebrow="Diseño del agente" title={mode === "company" ? "Empresa" : mode === "library" ? "Biblioteca de documentos" : "Calidad del agente"} description={mode === "company" ? "Identidad, reglas y servicios compartidos. Los puestos y sus requisitos se configuran en Ofertas." : mode === "library" ? "Una copia de cada documento. Decide desde cada oferta qué fuentes utilizar." : "Evalúa cómo trabaja el asistente, no el encaje del candidato. Los criterios de encaje se configuran en cada oferta."} actions={<div className="flex flex-wrap items-center gap-2"><Button variant="outline" onClick={discard} disabled={save.isPending || !dirty}>Descartar cambios</Button><Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}><Save size={14} />{save.isPending ? "Guardando…" : "Guardar borrador"}</Button></div>} />
    <div className="draft-status-strip"><span className="draft-status-icon"><FileText size={17} /></span><div><strong>Borrador · revisión {rev}</strong><p>{dirty ? "Tienes cambios locales pendientes de guardar." : `Trabajas sobre la configuración de ${tenant.name}.`}</p></div><span className="draft-save-state" role="status">{dirty ? "Sin guardar" : "Sin cambios locales"}</span><Link to={`/t/${tenantId}/cambios`}>Revisar cambios<ChevronRight size={14} /></Link></div>
    {save.isError && <Alert variant="destructive"><AlertDescription>{save.error.message}</AlertDescription></Alert>}
    {save.isSuccess && !dirty && <p role="status" className="text-sm text-emerald-700">Borrador guardado.</p>}
    <div className="profile-mobile-section"><Label htmlFor="profile-section">Sección del perfil</Label><select id="profile-section" value={section} onChange={e => setSection(e.target.value)}>{tabs.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></div>
    <Tabs value={section} onValueChange={setSection} orientation="vertical" className="profile-tabs"><TabsList aria-label="Secciones del perfil">{tabs.map(([id, label], index) => { const Icon = tabIcons[index] ?? FileText; return <TabsTrigger key={id} value={id}><Icon size={15} aria-hidden="true" />{label}</TabsTrigger>; })}</TabsList>
      <TabsContent value="brief" className="space-y-5 pt-4"><div className="section-intro"><div><h2>Identidad y políticas compartidas</h2><p>No incluyas aquí los requisitos particulares de un puesto.</p></div><span className="count-pill">{local.schema_version === 3 ? 4 : 6} secciones</span></div><div className="brief-grid">{briefSections.filter(([key]) => local.schema_version !== 3 || !["who", "fit"].includes(key)).map(([key, label, help], index) => <Card key={key} className="brief-card"><CardHeader><div className="brief-card-title"><span>{String(index + 1).padStart(2, "0")}</span><CardTitle><Label htmlFor={`brief-${key}`}>{key === "role" && local.schema_version === 3 ? "Empresa y propósito del asistente" : label}</Label></CardTitle></div><p className="text-sm text-muted-foreground">{key === "role" && local.schema_version === 3 ? "Qué hace la empresa y cómo se presenta el asistente. Sin requisitos de un puesto concreto." : help}</p></CardHeader><CardContent><Textarea id={`brief-${key}`} rows={6} value={local.brief[key] ?? ""} onChange={e => edit(p => { p.brief[key] = e.target.value; })} /></CardContent></Card>)}</div></TabsContent>
      <TabsContent value="operating" className="space-y-4 pt-4"><Card><CardHeader><CardTitle>Instrucciones comunes</CardTitle><p className="text-sm text-muted-foreground">Reglas compartidas por todas las ofertas. Los flujos propios de un puesto se editan en su entrevista.</p></CardHeader><CardContent><Textarea aria-label="Modelo operativo" rows={16} value={local.operating_model ?? ""} onChange={e => edit(p => { p.operating_model = e.target.value; })} /></CardContent></Card></TabsContent>
      <TabsContent value="sources" className="pt-4"><SourcesEditor profile={local} edit={edit} /></TabsContent>
      <TabsContent value="connections" className="pt-4"><ConnectionsEditor profile={local} edit={edit} /></TabsContent>
      <TabsContent value="channels" className="pt-4"><ChannelSettingsEditor profile={local} edit={edit} tenantId={tenantId} /></TabsContent>
      <TabsContent value="rubric" className="pt-4"><RubricEditor profile={local} edit={edit} /></TabsContent>
      <TabsContent value="grants" className="grid gap-4 pt-4 md:grid-cols-2">{(["conversador", "recruiter"] as const).map(agent => <Card key={agent}><CardHeader><CardTitle>{agent === "conversador" ? "Conversador" : "Asistente del reclutador"}</CardTitle></CardHeader><CardContent className="space-y-3">{capabilities[agent].map((cap: CapabilityId) => <Label key={cap} className="flex gap-2"><Checkbox checked={(local.grants[agent] ?? []).includes(cap)} onCheckedChange={checked => edit(p => { p.grants[agent] = checked === true ? [...(p.grants[agent] ?? []), cap] : (p.grants[agent] ?? []).filter(v => v !== cap); })} />{cap}</Label>)}</CardContent></Card>)}</TabsContent>
      <TabsContent value="result" className="space-y-4 pt-4"><Card><CardHeader><CardTitle>Campos comunes reutilizables</CardTitle><p>Solo se aplican a las ofertas que los heredan. Sus cambios no alteran las copias independientes.</p></CardHeader><CardContent><ResultSchemaEditor fields={local.result_schema ?? []} onChange={fields => edit(p => { p.result_schema = fields; })} /></CardContent></Card></TabsContent>
      <TabsContent value="autonomy" className="space-y-3 pt-4">{(Object.keys(autonomyLabels) as (keyof typeof autonomyLabels)[]).map(key => <Card key={key}><CardContent className="flex flex-wrap items-center justify-between gap-4 py-5"><div className="font-medium">{autonomyLabels[key]}</div><div className="flex flex-wrap gap-1">{autonomyLevels.map((label, level) => <Button key={level} size="sm" variant={local.autonomy[key] === level ? "default" : "outline"} disabled={level > (key === "reject" ? 2 : key === "brief" ? 1 : 3)} onClick={() => edit(p => { p.autonomy[key] = level; })}>{label}</Button>)}</div></CardContent></Card>)}</TabsContent>
      <TabsContent value="invariants" className="grid gap-4 pt-4 md:grid-cols-2">{invariants.map(([name, text]) => <Card key={name}><CardHeader><CardTitle>{name}</CardTitle></CardHeader><CardContent><p className="text-sm leading-relaxed text-muted-foreground">{text}</p></CardContent></Card>)}</TabsContent>
    </Tabs>
    {mode === "company" && (section === "brief" || section === "operating") && <CopilotSuggestions key={section} tenantId={tenantId} section={section === "brief" ? "brief" : "operating_model"} edit={edit} />}
  </fieldset>;
}
