import type { ReactNode } from "react";
import type { ProfileContent } from "@/lib/profile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

export type ResultField = NonNullable<ProfileContent["result_schema"]>[number];
export function ResultSchemaEditor({ fields, onChange, criteria }: {
  fields: ResultField[];
  onChange: (fields: ResultField[]) => void;
  criteria?: (field: ResultField) => ReactNode;
}) {
  const update = (index: number, change: Partial<ResultField>) =>
    onChange(fields.map((f, i) => i === index ? { ...f, ...change } : f));
  return <div className="space-y-5">
    <p className="workspace-note">Recoge solo información profesional necesaria. Los formatos se verifican antes de registrar una decisión; el encaje se valora con criterios separados.</p>
    {fields.map((field, index) => <fieldset className="config-item space-y-4" key={index}>
      <legend>Dato {index + 1} · {field.label || field.description || "Nuevo dato"}</legend>
      <div className="form-two">
        <label className="form-label">Nombre visible para selección<Input maxLength={100} value={field.label ?? ""} onChange={e => update(index, { label: e.target.value })} /></label>
        <label className="form-label">Dato que se pregunta<Input required maxLength={200} value={field.description} onChange={e => update(index, { description: e.target.value })} /></label>
      </div>
      <div className="form-two">
        <label className="form-label">Tipo de dato<select value={field.value_type ?? "text"} onChange={e => update(index, {
          value_type: e.target.value as ResultField["value_type"], minimum: null,
          choices: e.target.value === "enum" ? ["Opción 1"] : [], future_date: false, exclude_if_false: false,
        })}><option value="text">Texto</option><option value="boolean">Sí / No</option><option value="number">Número</option><option value="enum">Opciones cerradas</option><option value="date">Fecha</option></select></label>
        {field.value_type === "enum" && <label className="form-label">Opciones, una por línea<textarea value={(field.choices ?? []).join("\n")} onChange={e => update(index, { choices: e.target.value.split("\n") })} /></label>}
        {field.value_type === "number" && <label className="form-label">Mínimo válido del dato<Input type="number" value={field.minimum ?? ""} onChange={e => update(index, { minimum: e.target.value === "" ? null : Number(e.target.value) })} /><small>Valida el formato, no puntúa el encaje. Por ejemplo, 0 para años de experiencia.</small></label>}
      </div>
      <label><input type="checkbox" checked={field.required ?? true} onChange={e => update(index, { required: e.target.checked })} /> Necesario para completar la entrevista</label>
      {field.value_type === "date" && <label className="block"><input type="checkbox" checked={field.future_date ?? false} onChange={e => update(index, { future_date: e.target.checked })} /> No admite fechas pasadas</label>}
      {field.value_type === "boolean" && <label className="block"><input type="checkbox" checked={field.exclude_if_false ?? false} onChange={e => update(index, { exclude_if_false: e.target.checked })} /> Un «No» propone revisión de no apto, nunca descarte automático</label>}
      <details className="field-advanced"><summary>Identificación e integración</summary><div className="form-two mt-4">
        <label className="form-label">Identificador<Input aria-label={`Campo ${index + 1}`} required pattern="[a-z][a-z0-9_]{0,39}" value={field.key} onChange={e => update(index, { key: e.target.value })} /></label>
        <label className="form-label">Campo en el ATS<Input value={field.ats_field ?? ""} onChange={e => update(index, { ats_field: e.target.value })} /></label>
        <label className="form-label">Uso en filtros de selección<select value={field.semantic ?? ""} onChange={e => update(index, { semantic: e.target.value as ResultField["semantic"] })}>{Object.entries({ "": "Sin uso especial", name: "Nombre", location: "Zona", site: "Centro", shift: "Turno", availability: "Disponibilidad", start_date: "Incorporación" }).map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>
      </div></details>
      {criteria?.(field)}
      <Button type="button" variant="ghost" onClick={() => onChange(fields.filter((_, i) => i !== index))}>Quitar campo</Button>
    </fieldset>)}
    <Button type="button" variant="outline" disabled={fields.length >= 40} onClick={() => { const key = `campo_${crypto.randomUUID().slice(0, 8)}`; onChange([...fields, { key, description: "Nuevo dato", label: "", ats_field: key, required: true, value_type: "text", choices: [], future_date: false, exclude_if_false: false, semantic: "" }]); }}>Añadir campo</Button>
  </div>;
}
