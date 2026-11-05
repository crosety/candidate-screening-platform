import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { apiError } from "@/lib/errors";
import { briefSections, type ProfileContent } from "@/lib/profile";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ChevronDown, Sparkles } from "lucide-react";

export function CopilotSuggestions({ tenantId, section, edit }: {
  tenantId: string; section: "brief" | "operating_model";
  edit: (change: (profile: ProfileContent) => void) => void;
}) {
  const [added, setAdded] = useState<number[]>([]);
  const suggestions = useMutation({ mutationFn: async () => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/copilot/suggestions", {
      params: { path: { tenant_id: tenantId } }, body: { section },
    });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: () => setAdded([]) });
  return <details className="surface copilot-disclosure" aria-label="Copiloto del FDE"><summary><span className="assistant-icon"><Sparkles size={16} /></span><span><strong>Copiloto del FDE</strong><small>Una segunda mirada a las instrucciones del agente.</small></span><ChevronDown size={15} className="disclosure-chevron" /></summary><div className="copilot-content space-y-3">
    <Button variant="outline" disabled={suggestions.isPending} onClick={() => suggestions.mutate()}>{suggestions.isPending ? "Pensando…" : "Sugerencias del copiloto"}</Button>
    <p className="text-xs text-muted-foreground">Usa el borrador guardado. Solo propone: añadir no guarda ni publica.</p>
    {suggestions.error && <p role="alert" className="text-destructive">{suggestions.error.message}</p>}
    {suggestions.isSuccess && !suggestions.data.length && <p role="status">El copiloto no ve nada que mejorar ahora.</p>}
    {suggestions.data?.map((item, index) => <Card key={index}><CardHeader><CardTitle>{item.field === "operating_model" ? "Modelo operativo" : briefSections.find(([key]) => key === item.field)?.[1] ?? item.field}</CardTitle></CardHeader><CardContent className="space-y-3"><p className="text-sm text-muted-foreground">{item.why}</p><p className="text-sm">{item.append}</p><Button variant="outline" disabled={added.includes(index)} onClick={() => {
      edit(profile => {
        if (item.field === "operating_model" && section === "operating_model") profile.operating_model = `${profile.operating_model ?? ""}\n${item.append}`.trim();
        else if (section === "brief" && briefSections.some(([key]) => key === item.field)) {
          const field = item.field as keyof ProfileContent["brief"];
          profile.brief[field] = `${profile.brief[field] ?? ""}\n${item.append}`.trim();
        }
      }); setAdded(current => [...current, index]);
    }}>{added.includes(index) ? "Añadida al borrador local" : "Añadir al borrador"}</Button></CardContent></Card>)}
  </div></details>;
}
