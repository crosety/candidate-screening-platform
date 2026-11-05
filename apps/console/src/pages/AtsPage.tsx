import { Fragment, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { PageHeader } from "@/components/WorkspaceUI";
import { useTenant } from "@/layout/AppLayout";
import { apiError } from "@/lib/errors";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useOffers } from "./OffersPage";

const blank = { candidate_name: "", phone: "", city: "", job_ref: "" };

export function AtsPage() {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [form, setForm] = useState(blank);
  const [copyStatus, setCopyStatus] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const offers = useOffers();
  const configured = offers.data?.offers.some(offer => offer.configured) ?? false;
  const available = offers.data?.offers.filter(offer => offer.published?.status === "open") ?? [];
  const canCreate = !!offers.data && !offers.error && (!configured || available.some(offer => offer.key === form.job_ref));
  const records = useQuery({ queryKey: ["ats", tenantId], refetchInterval: 5000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/simulated/ats", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const create = useMutation({ mutationFn: async () => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/simulated/ats", { params: { path: { tenant_id: tenantId } }, body: form });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }, onSuccess: () => { setForm(blank); void cache.invalidateQueries({ queryKey: ["ats", tenantId] }); },
  onError: () => { void cache.invalidateQueries({ queryKey: ["offers", tenantId] }); }});
  async function copy(link: string) {
    try { await navigator.clipboard.writeText(link); setCopyStatus("Enlace copiado."); }
    catch { setCopyStatus("No se ha podido copiar. Selecciona el enlace para copiarlo."); }
  }
  const errorMessage = offers.error
    ? "No se pueden cargar las ofertas. Reintenta la carga para continuar."
    : (records.error ?? create.error)?.message;
  return <div className="space-y-6 fde-page"><PageHeader eyebrow="Operación" title="ATS simulado" description="Consulta el resultado de cada candidatura o crea una nueva para probar el recorrido completo." actions={<Badge variant="outline">Solo datos sintéticos</Badge>} />{errorMessage && <Alert variant="destructive"><AlertDescription>{errorMessage}{offers.error && <Button type="button" variant="outline" size="sm" disabled={offers.isFetching} onClick={() => { void offers.refetch(); }}>{offers.isFetching ? "Reintentando…" : "Reintentar ofertas"}</Button>}</AlertDescription></Alert>}{copyStatus && <p className="text-sm" role="status">{copyStatus}</p>}
    <Card><CardContent><Table><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>Nombre</TableHead><TableHead>Ciudad</TableHead><TableHead>Puesto</TableHead><TableHead>Etapa</TableHead><TableHead>Campos</TableHead><TableHead>Actualización</TableHead><TableHead>Enlace del candidato</TableHead></TableRow></TableHeader><TableBody>{records.data?.map(row => <Fragment key={row.id}><TableRow className="cursor-pointer" onClick={() => setExpanded(expanded === row.id ? null : row.id)}><TableCell className="font-mono text-xs">{row.external_id}</TableCell><TableCell className="font-medium"><button type="button" aria-expanded={expanded === row.id}>{row.candidate_name} · Ver notas</button></TableCell><TableCell>{row.city}</TableCell><TableCell>{row.job_ref}</TableCell><TableCell><Badge variant="secondary">{row.stage}</Badge></TableCell><TableCell className="whitespace-normal text-xs">{Object.entries(row.fields).map(([key, value]) => <p key={key}>{key}: {String(value)}</p>)}</TableCell><TableCell className="text-xs">{new Date(row.updated_at).toLocaleString("es")}</TableCell><TableCell>{row.candidate_link ? <div className="flex gap-2"><Button variant="outline" size="sm" onClick={e => { e.stopPropagation(); void copy(row.candidate_link!); }}>Copiar</Button><Button variant="outline" size="sm" asChild><a href={row.candidate_link} target="_blank" rel="noreferrer">Abrir chat</a></Button></div> : <span className="text-muted-foreground">Sin enlace</span>}</TableCell></TableRow>{expanded === row.id && <TableRow><TableCell colSpan={8} className="whitespace-normal"><h3 className="font-medium">Notas</h3>{row.notes.map((raw, index) => { const note = raw as { at: string; text: string }; return <p key={index} className="mt-2 text-sm">{new Date(note.at).toLocaleString("es")} · {note.text}</p>; })}{!row.notes.length && <p className="text-muted-foreground">Sin notas.</p>}</TableCell></TableRow>}</Fragment>)}</TableBody></Table>{records.isPending && <p className="py-5 text-sm text-muted-foreground">Cargando ATS…</p>}</CardContent></Card>
    <Card><CardHeader><CardTitle>Nueva candidatura de prueba</CardTitle></CardHeader><CardContent>
      <form className="grid gap-4 md:grid-cols-2" onSubmit={e => { e.preventDefault(); if (canCreate) create.mutate(); }}>
        {([ ["candidate_name", "Nombre"], ["phone", "Teléfono"], ["city", "Ciudad"] ] as const).map(([key, label]) => <div key={key} className="space-y-2"><Label htmlFor={`ats-${key}`}>{label}</Label><Input id={`ats-${key}`} required={key !== "phone"} maxLength={key === "candidate_name" ? 200 : key === "phone" ? 30 : 120} value={form[key]} onChange={e => setForm(current => ({ ...current, [key]: e.target.value }))} /></div>)}
        <div className="space-y-2">
          {offers.isPending ? <p className="text-sm text-muted-foreground" role="status">Cargando ofertas…</p>
            : offers.error && !offers.data ? <p className="text-sm text-muted-foreground">Vuelve a cargar las ofertas para elegir el puesto.</p>
            : configured ? <><Label htmlFor="ats-offer">Oferta publicada</Label><select id="ats-offer" className="flex h-9 w-full min-w-0 rounded-md border border-input bg-transparent px-3 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]" required value={form.job_ref} onChange={e => setForm(current => ({ ...current, job_ref: e.target.value }))}><option value="">Elige una oferta</option>{available.map(offer => <option key={offer.key} value={offer.key}>{offer.published!.title}</option>)}</select>{available.length === 0 && <p className="text-sm text-muted-foreground">Publica y abre una oferta antes de crear una candidatura de prueba.</p>}</>
            : <><Label htmlFor="ats-job_ref">Puesto</Label><Input id="ats-job_ref" required disabled={!offers.data || !!offers.error} maxLength={60} value={form.job_ref} onChange={e => setForm(current => ({ ...current, job_ref: e.target.value }))} /></>}
        </div>
        <div className="md:col-span-2"><Button type="submit" disabled={create.isPending || !canCreate}>{create.isPending ? "Creando…" : "Crear candidatura"}</Button></div>
      </form>{create.data && <div className="mt-5 rounded-lg border bg-muted/40 p-4" role="status"><p className="mb-2 text-sm font-medium">Candidatura {create.data.external_id} creada</p><a className="break-all text-sm underline underline-offset-4" href={create.data.candidate_link} target="_blank" rel="noreferrer">{create.data.candidate_link}</a></div>}
    </CardContent></Card>
  </div>;
}
