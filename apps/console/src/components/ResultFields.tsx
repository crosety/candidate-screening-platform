import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";

// Client field keys come from each profile's schema; only platform keys are labelled here.
const platformLabels: Record<string, string> = { credential: "Acreditación", certificate: "Certificado", reason: "Motivo de la evaluación" };
const humanize = (key: string) => key.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());

export function ResultFields({ fields, layout = "table", fieldLabels = {} }: { fields: Record<string, unknown>; layout?: "table" | "cards"; fieldLabels?: Record<string, string> }) {
  const labels: Record<string, string> = { ...platformLabels, ...fieldLabels };
  const confidenceLabels: Record<string, string> = { high: "Alta", medium: "Media", low: "Baja" };
  labels.__reason = labels.reason;
  if (layout === "cards") return <div className="evidence-grid">{Object.entries(fields).map(([key, raw]) => {
    const field = raw !== null && typeof raw === "object" ? raw as { value?: unknown; evidence?: unknown; confidence?: unknown } : { value: raw };
    return <article key={key} className={`evidence-card ${key === "reason" || key === "__reason" ? "evidence-card-wide" : ""}`}><header><span>{labels[key] ?? humanize(key)}</span>{field.confidence ? <span className={`confidence confidence-${String(field.confidence)}`}>{confidenceLabels[String(field.confidence)] ?? String(field.confidence)}</span> : null}</header><p>{String(field.value ?? "No indicado")}</p>{field.evidence ? <blockquote>«{String(field.evidence)}»</blockquote> : null}</article>;
  })}</div>;
  return <Table><TableHeader><TableRow><TableHead>Campo</TableHead><TableHead>Valor</TableHead><TableHead>Cita</TableHead><TableHead>Confianza</TableHead></TableRow></TableHeader><TableBody>{Object.entries(fields).map(([key, raw]) => {
    const field = raw !== null && typeof raw === "object" ? raw as { value?: unknown; evidence?: unknown; confidence?: unknown } : { value: raw };
    return <TableRow key={key}><TableCell className="font-medium">{labels[key] ?? humanize(key)}</TableCell><TableCell className="whitespace-normal min-w-24">{String(field.value ?? "—")}</TableCell><TableCell className="whitespace-normal min-w-40 text-muted-foreground">{field.evidence ? `«${String(field.evidence)}»` : "Sin evidencia"}</TableCell><TableCell><span className={`confidence confidence-${String(field.confidence)}`}>{confidenceLabels[String(field.confidence)] ?? "—"}</span></TableCell></TableRow>;
  })}</TableBody></Table>;
}
