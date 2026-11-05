import type { components } from "@screening/api-client";
import type { ResultField } from "./ResultSchemaEditor";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
type Criterion = components["schemas"]["FitCriterion"];

export function OfferCriteria({ field, items, onChange }: { field: ResultField; items: Criterion[]; onChange: (items: Criterion[]) => void }) {
  const update = (index: number, patch: Partial<Criterion>) => onChange(items.map((c, i) => index === i ? { ...c, ...patch } : c));
  const numeric = field.value_type === "number";
  return <div className="offer-criteria space-y-3"><h3>Encaje · {field.label || field.description}</h3>
    {!items.length && <p className="workspace-note">Este dato se recoge, pero no afecta a la puntuación.</p>}
    {items.map((c, i) => <div className="space-y-3" key={c.key}>
      <label className="form-label">Criterio profesional<Input value={c.label} onChange={e => update(i, { label: e.target.value })} /></label>
      <div className="form-two"><label className="form-label">Condición<select value={c.operator} onChange={e => update(i, { operator: e.target.value as Criterion["operator"], expected: e.target.value === "present" ? null : numeric ? 1 : field.value_type === "boolean" ? true : "" })}><option value="present">Dato aportado</option><option value="equals">Es igual a</option>{numeric && <option value="minimum">Como mínimo</option>}</select></label>
      {c.operator !== "present" && <label className="form-label">Valor esperado{field.value_type === "boolean" ? <select value={String(c.expected)} onChange={e => update(i, { expected: e.target.value === "true" })}><option value="true">Sí</option><option value="false">No</option></select> : <Input type={numeric ? "number" : "text"} value={String(c.expected ?? "")} onChange={e => update(i, { expected: numeric ? Number(e.target.value) : e.target.value })} />}</label>}
      <label className="form-label">Peso (1–10)<Input type="number" min={1} max={10} value={c.weight} onChange={e => update(i, { weight: Number(e.target.value) })} /></label></div>
      <label><input type="checkbox" checked={c.required} onChange={e => update(i, { required: e.target.checked })} /> Requisito imprescindible</label>
      <Button type="button" variant="ghost" onClick={() => onChange(items.filter((_, n) => n !== i))}>Quitar criterio</Button>
    </div>)}
    <Button type="button" size="sm" variant="outline" onClick={() => onChange([...items, { key: `criterio_${crypto.randomUUID().slice(0, 8)}`, label: field.description, field: field.key, operator: "present", expected: null, weight: 1, required: false }])}>Añadir criterio de encaje</Button>
  </div>;
}
