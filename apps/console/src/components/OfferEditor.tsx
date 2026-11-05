import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api, type components } from "@screening/api-client";
import { Save } from "lucide-react";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { useUnsavedChanges } from "@/lib/useUnsavedChanges";
import { ResultSchemaEditor, type ResultField } from "./ResultSchemaEditor";
import { OfferCriteria } from "./OfferCriteria";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
type Definition = components["schemas"]["OfferDefinition"];
type Offers = components["schemas"]["OffersOut"];
const tabs = [["position", "Puesto y condiciones"], ["interview", "Entrevista y evaluación"], ["resources", "Documentación y herramientas"], ["preview", "Probar y publicar"]] as const;

export function OfferEditor({ initial, source, creating }: { initial: Definition; source: Offers; creating: boolean }) {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const [form, setForm] = useState<Definition>(() => ({ ...initial, fields_mode: initial.fields_mode ?? (initial.fields?.length ? "custom" : "inherit") }));
  const [rev, setRev] = useState(source.rev ?? 1);
  const [tab, setTab] = useState<(typeof tabs)[number][0]>("position");
  const [dirty, setDirty] = useState(creating);
  const allowSavedNavigation = useUnsavedChanges(dirty);
  const [saved, setSaved] = useState(!creating && source.offers.some(o => o.key === initial.key && o.has_changes));
  const [metadata, setMetadata] = useState(Object.entries(initial.dimensions ?? {}));
  const sources = source.sources ?? [];
  const fields = form.fields_mode === "custom" ? form.fields ?? [] : source.shared_fields;
  const criteria = form.criteria ?? [];
  const own = form.fields_mode === "custom";
  const update = (value: Partial<Definition>) => { setForm(current => ({ ...current, ...value })); setDirty(true); setSaved(false); preview.reset(); };
  function definition(): Definition {
    if (!form.title.trim()) { setTab("position"); throw new Error("Escribe el nombre de la oferta."); }
    if (!/^[a-zA-Z0-9_-]{1,60}$/.test(form.key)) { setTab("position"); throw new Error("Referencia estable: usa de 1 a 60 letras, números, guiones o guiones bajos, sin espacios."); }
    for (const f of fields) {
      if (!f.description.trim() || !/^[a-z][a-z0-9_]{0,39}$/.test(f.key)) { setTab("interview"); throw new Error(`Revisa el dato «${f.label || f.key}»: necesita una pregunta y una clave válida (letras minúsculas, números y guion bajo).`); }
      if (f.value_type === "enum" && !f.choices?.length) { setTab("interview"); throw new Error(`Añade las opciones de «${f.label || f.key}».`); }
    }
    if (new Set(fields.map(f => f.key)).size !== fields.length) { setTab("interview"); throw new Error("Cada dato de la entrevista necesita una clave distinta."); }
    for (const c of criteria) {
      if (!c.label.trim() || (c.weight ?? 1) < 1 || (c.weight ?? 1) > 10 || (c.operator !== "present" && (c.expected === null || c.expected === undefined || c.expected === ""))) { setTab("interview"); throw new Error(`Revisa el criterio «${c.label || c.key}»: indica su nombre, valor esperado y un peso entre 1 y 10.`); }
    }
    const names = metadata.map(([k]) => k.trim()).filter(Boolean);
    if (new Set(names).size !== names.length) throw new Error("No repitas el nombre de una etiqueta.");
    return { ...form, dimensions: Object.fromEntries(metadata.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), v])) };
  }
  const save = useMutation({ mutationFn: async () => {
    if (creating && source.offers.some(o => o.key === form.key)) throw new Error("Ya existe una oferta con esta referencia.");
    const r = await api.PUT("/api/v1/tenants/{tenant_id}/offers/{offer_key}", { params: { path: { tenant_id: tenantId, offer_key: form.key } }, headers: creating ? { "If-None-Match": "*" } : undefined, body: { rev, definition: definition() } });
    if (!r.data) throw new Error(apiError(r.error)); return r.data;
  }, onSuccess: data => {
    setRev(data.rev ?? rev); setDirty(false); setSaved(true); preview.reset();
    cache.setQueryData(["offers", tenantId], data);
    for (const key of ["draft", "diff", "checks", "gate"]) void cache.invalidateQueries({ queryKey: [key, tenantId] });
    void cache.invalidateQueries({ queryKey: ["tenants"] });
    if (creating) { allowSavedNavigation(); navigate(`/t/${tenantId}/ofertas/${encodeURIComponent(form.key)}`, { replace: true }); }
  } });
  const preview = useMutation({ mutationFn: async () => {
    const r = await api.POST("/api/v1/tenants/{tenant_id}/offers/preview", { params: { path: { tenant_id: tenantId } }, body: { rev, definition: definition() } });
    if (!r.data) throw new Error(apiError(r.error)); return r.data;
  } });
  function changeFields(next: ResultField[]) {
    const updated = criteria.flatMap(c => {
      const before = fields.findIndex(f => f.key === c.field);
      const after = fields.length === next.length ? next[before] : next.find(f => f.key === c.field);
      if (!after) return [];
      const typeChanged = fields[before]?.value_type !== after.value_type;
      return [{ ...c, field: after.key, ...(typeChanged ? { operator: "present" as const, expected: null } : {}) }];
    });
    update({ fields: next, fields_mode: "custom", criteria: updated });
  }
  function changeMetadata(next: [string, string][]) { setMetadata(next); setDirty(true); setSaved(false); preview.reset(); }
  function inheritFields() {
    if (!window.confirm("Se sustituirán los campos propios por los comunes. Se eliminarán los criterios sin campo compatible y los de tipos distintos pasarán a comprobar presencia. ¿Continuar?")) return;
    const next = criteria.flatMap(c => {
      const shared = source.shared_fields.find(f => f.key === c.field);
      if (!shared) return [];
      const previous = fields.find(f => f.key === c.field);
      return [{ ...c, ...(previous?.value_type !== shared.value_type ? { operator: "present" as const, expected: null } : {}) }];
    });
    update({ fields_mode: "inherit", fields: [], criteria: next });
  }
  const renderCriteria = (field: ResultField) => <OfferCriteria field={field} items={criteria.filter(c => c.field === field.key)} onChange={next => update({ criteria: [...criteria.filter(c => c.field !== field.key), ...next] })} />;
  return <fieldset disabled={save.isPending} className="offer-config space-y-5 min-w-0">
    <div className="draft-status-strip"><div><strong>Borrador · revisión {rev}</strong><p>{dirty ? "Cambios sin guardar" : "Las conversaciones actuales conservan su versión."}</p></div><Button onClick={() => save.mutate()} disabled={save.isPending || (!dirty && !creating)}><Save size={14} />{save.isPending ? "Guardando…" : "Guardar borrador"}</Button></div>
    {saved && <p role="status">Borrador guardado. Falta validar y publicar.</p>}
    {save.error && <p className="inline-error" role="alert">{save.error.message}</p>}
    {preview.error && <p role="alert" className="inline-error">{preview.error.message}</p>}
    <nav className="workspace-tabs offer-config-tabs" aria-label="Configuración de la oferta">{tabs.map(([key, name]) => <button key={key} className={tab === key ? "active" : ""} aria-current={tab === key ? "page" : undefined} onClick={() => setTab(key)}>{name}</button>)}</nav>
    {tab === "position" && <section className="candidate-panel space-y-5"><h2>Puesto y condiciones</h2><p>La empresa aporta la identidad y las políticas comunes. Aquí se define únicamente esta oportunidad.</p>
      <div className="form-two"><label className="form-label">Nombre de la oferta<Input value={form.title} maxLength={200} onChange={e => update({ title: e.target.value })} /></label><label className="form-label">Estado al publicar<select value={form.status} onChange={e => update({ status: e.target.value as Definition["status"] })}><option value="draft">Borrador</option><option value="open">Abierta</option><option value="paused">Pausada</option><option value="closed">Cerrada</option></select></label></div>
      <label className="form-label">Puesto, condiciones y requisitos<textarea rows={9} maxLength={12000} value={form.description ?? ""} onChange={e => update({ description: e.target.value })} placeholder="Responsabilidades, condiciones, requisitos y lo que necesita saber el candidato." /></label>
      <details open={creating || undefined}><summary>Referencia y organización opcional</summary><div className="space-y-4 mt-4"><label className="form-label">Referencia estable<Input disabled={!creating} value={form.key} onChange={e => update({ key: e.target.value })} /></label><p>Las etiquetas organizan la oferta; no filtran herramientas ni conceden permisos.</p>{metadata.map(([key, value], i) => <div className="metadata-row" key={i}><Input aria-label={`Nombre de etiqueta ${i + 1}`} value={key} onChange={e => changeMetadata(metadata.map((r, n) => n === i ? [e.target.value, value] : r))} /><Input aria-label={`Valor de etiqueta ${i + 1}`} value={value} onChange={e => changeMetadata(metadata.map((r, n) => n === i ? [key, e.target.value] : r))} /><Button variant="ghost" onClick={() => changeMetadata(metadata.filter((_, n) => n !== i))}>Quitar</Button></div>)}<Button variant="outline" disabled={metadata.length >= 12} onClick={() => changeMetadata([...metadata, ["", ""]])}>Añadir etiqueta</Button></div></details>
    </section>}
    {tab === "interview" && <section className="candidate-panel space-y-6"><h2>Entrevista y evaluación del candidato</h2>
      <label className="form-label">Instrucciones de la entrevista<textarea rows={5} maxLength={12000} value={form.instructions ?? ""} onChange={e => update({ instructions: e.target.value })} placeholder="Orden, aclaraciones, comprobaciones y cuándo terminar o derivar." /></label>
      <div className="config-origin"><strong>{own ? "Campos propios de esta oferta" : "Campos heredados de la empresa"}</strong><p>{own ? "Copia independiente: los cambios de campos comunes no se incorporan. Si la lista queda vacía, no se reactivan los comunes." : "Se actualizan al cambiar los campos comunes del cliente. Los criterios de encaje siguen siendo propios de esta oferta."}</p>
        {!own ? <Button variant="outline" onClick={() => update({ fields_mode: "custom", fields: structuredClone(fields) })}>Crear copia independiente</Button> : <Button variant="outline" onClick={inheritFields}>Usar campos comunes</Button>}
      </div>
      <p className="workspace-note">Los criterios ponderan la puntuación de encaje. Los requisitos imprescindibles se muestran aparte; un dato pendiente no cuenta como incumplimiento.</p>
      {own ? <ResultSchemaEditor fields={fields} onChange={changeFields} criteria={renderCriteria} /> : <><Link to={`/t/${tenantId}/perfil`}>Editar los campos comunes en Empresa</Link>{fields.map(f => <section className="config-item space-y-4" key={f.key}><h3>{f.label || f.description}</h3><p>{f.description} · {f.required ? "Necesario" : "Opcional"} · Heredado</p>{renderCriteria(f)}</section>)}</>}
    </section>}
    {tab === "resources" && <section className="candidate-panel space-y-6"><h2>Documentación y herramientas</h2><p>Selecciona fuentes de la biblioteca compartida. No se crean copias; una modificación del documento afecta a las ofertas que lo usan.</p>
      <label><input type="checkbox" checked={form.source_ids == null} disabled={source.schema_version === 3} onChange={e => update({ source_ids: e.target.checked ? null : sources.map(s => s.id) })} /> Herencia de todas las fuentes (configuración anterior)</label>
      {sources.map(s => <label className="resource-choice" key={s.id}><input type="checkbox" checked={form.source_ids == null || form.source_ids.includes(s.id)} onChange={e => { const ids = form.source_ids ?? sources.map(d => d.id); update({ source_ids: e.target.checked ? [...ids, s.id] : ids.filter(id => id !== s.id) }); }} /><span><strong>{s.name}</strong><small>{!s.in_context && source.schema_version === 3 ? "Desactivada en la biblioteca" : s.mode === "search" ? "Búsqueda documental" : "Contexto de la entrevista"}</small></span></label>)}
      <Link to={`/t/${tenantId}/fuentes`}>Abrir biblioteca de documentos</Link><hr />
      <h3>Herramientas autorizadas</h3><p>La oferta puede limitar los permisos del cliente, nunca ampliarlos. Las instrucciones no conceden herramientas.</p>
      <label><input type="checkbox" checked={form.capabilities == null} onChange={e => update({ capabilities: e.target.checked ? null : [...source.allowed_capabilities] as NonNullable<Definition["capabilities"]> })} /> Heredar permisos de la empresa</label>
      {source.allowed_capabilities.map(cap => <label className="resource-choice" key={cap}><input type="checkbox" disabled={form.capabilities == null} checked={form.capabilities == null || form.capabilities.includes(cap as NonNullable<Definition["capabilities"]>[number])} onChange={e => update({ capabilities: (e.target.checked ? [...form.capabilities!, cap] : form.capabilities!.filter(c => c !== cap)) as NonNullable<Definition["capabilities"]> })} />{cap}</label>)}
      <p className="workspace-note">Los sistemas y su alcance se configuran en Empresa → Conexiones. Las etiquetas de la oferta no restringen los datos devueltos por una herramienta.</p>
    </section>}
    {tab === "preview" && <section className="candidate-panel space-y-5"><h2>Probar y publicar</h2><p>Revisa la combinación real de empresa y oferta, incluidos los cambios locales. La vista previa no ejecuta modelos ni publica.</p><Button variant="outline" onClick={() => preview.mutate()} disabled={preview.isPending}>Actualizar vista previa</Button>
      {preview.data && <><div className="offer-preview-summary"><div><strong>{preview.data.fields.length}</strong><span>Campos {preview.data.fields_origin === "inherit" ? "heredados" : "propios"}</span></div><div><strong>{preview.data.sources.length}</strong><span>Fuentes activas</span></div><div><strong>{preview.data.capabilities.length}</strong><span>Herramientas</span></div></div>
        <p>Fuentes: {preview.data.sources.map(s => s.name).join(", ") || "Ninguna"}</p><details><summary>Ver instrucciones efectivas · chat web</summary><pre className="effective-prompt">{preview.data.prompt}</pre></details>
        <div className="config-origin"><strong>Una publicación conjunta para el cliente</strong><p>La versión abarca estas ofertas: {preview.data.affected_offers.join(", ")}. No implica que todas hayan cambiado. La vista previa incluye tus cambios locales; solo los guardados entran en la publicación.</p></div></>}
      {source.common_changes && <p className="draft-note">También hay cambios comunes de empresa o biblioteca pendientes de publicar.</p>}
      {dirty ? <p>Guarda el borrador antes de continuar a las pruebas.</p> : <div className="flex flex-wrap gap-3"><Button variant="outline" asChild><Link to={`/t/${tenantId}/simulacion?tab=evaluaciones`}>Validar con simulaciones</Link></Button><Button variant="outline" asChild><Link to={`/t/${tenantId}/cambios`}>Revisar todos los cambios</Link></Button><Button asChild><Link to={`/t/${tenantId}/publicar`}>Validar y publicar</Link></Button></div>}
      <p className="workspace-note">La puntuación del candidato pertenece a esta oferta. La calidad del agente se valida en Simulación → Calidad del agente.</p>
    </section>}
  </fieldset>;
}
