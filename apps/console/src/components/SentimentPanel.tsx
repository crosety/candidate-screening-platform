export function SentimentPanel({ value }: { value: unknown }) {
  const items = Array.isArray(value) ? value as { stage: string; label: string; evidence: string | null }[] : [];
  return <section className="rounded-xl border bg-background p-5 space-y-3"><h2 className="font-semibold">Sentimiento por etapa</h2><p className="text-xs text-muted-foreground">Diagnóstico de conversación; no se usa como criterio de selección.</p>{items.length ? items.map((i, n) => <p key={n} className="text-sm">{stateLabel(i.stage)} · {stateLabel(i.label)}{i.evidence ? ` · «${i.evidence}»` : " · Sin evidencia suficiente"}</p>) : <p className="text-sm text-muted-foreground">Sin observaciones registradas.</p>}</section>;
}
import { stateLabel } from "@/lib/states";
