import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import type { ProfileContent } from "@/lib/profile";
import { capabilityCatalog, profileId } from "@/lib/capabilities";
import { apiError } from "@/lib/errors";
import { useTenant } from "@/layout/AppLayout";
import type { EditorProps } from "./ProfileEditors";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";

type Connection = NonNullable<ProfileContent["connections"]>[number];
const kinds: Connection["kind"][] = ["ats", "data", "calendar", "geo", "notify", "registry"];
const blank = (): Connection => ({ id: "", name: "", kind: "data", system: "", transport: "native", permission: "read", secret_ref: null, backs: [], segments: [], settings: { driver: "sim_sites" } });

function NewConnection({ profile, edit, close }: EditorProps & { close: () => void }) {
  const [form, setForm] = useState<Connection>(blank);
  const [extraKey, setExtraKey] = useState("");
  const [extraValue, setExtraValue] = useState("");
  const id = profileId(form.name, (profile.connections ?? []).map(c => c.id), "conexion");
  function settings(key: string, value: string) { setForm(current => ({ ...current, settings: { ...current.settings, [key]: value } })); }
  return <Card><CardHeader><CardTitle>Nueva conexión</CardTitle></CardHeader><CardContent><form className="grid gap-4 md:grid-cols-2" onSubmit={e => {
    e.preventDefault(); const values = { ...form.settings };
    if (form.transport === "mcp") delete values.driver; else { delete values.url; delete values.tool; }
    edit(p => { p.connections = [...(p.connections ?? []), { ...form, id, settings: values }]; }); close();
  }}><Label>Nombre de la conexión<Input required maxLength={120} value={form.name} onChange={e => setForm({ ...form, name: e.target.value })} /></Label><Label>Sistema<Input required maxLength={160} value={form.system} onChange={e => setForm({ ...form, system: e.target.value })} /></Label><div className="space-y-2"><Label>Tipo</Label><Select value={form.kind} onValueChange={kind => setForm({ ...form, kind: kind as Connection["kind"], backs: [] })}><SelectTrigger aria-label="Tipo de conexión"><SelectValue /></SelectTrigger><SelectContent>{kinds.map(kind => <SelectItem key={kind} value={kind}>{kind}</SelectItem>)}</SelectContent></Select></div><div className="space-y-2"><Label>Transporte</Label><Select value={form.transport ?? "native"} onValueChange={value => setForm({ ...form, transport: value === "mcp" ? "mcp" : "native" })}><SelectTrigger aria-label="Transporte de la conexión"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="native">Nativo</SelectItem><SelectItem value="mcp">MCP</SelectItem></SelectContent></Select></div>{form.transport === "mcp" ? <><Label>URL del MCP<Input required type="url" value={form.settings?.url ?? ""} onChange={e => settings("url", e.target.value)} /></Label><Label>Herramienta MCP<Input required value={form.settings?.tool ?? ""} onChange={e => settings("tool", e.target.value)} /></Label></> : <div className="space-y-2"><Label>Simulador</Label><Select value={form.settings?.driver ?? "sim_sites"} onValueChange={value => settings("driver", value)}><SelectTrigger aria-label="Simulador"><SelectValue /></SelectTrigger><SelectContent>{["sim_sites", "sim_ats", "sim_calendar"].map(driver => <SelectItem key={driver} value={driver}>{driver}</SelectItem>)}</SelectContent></Select></div>}<Label>Referencia de credenciales<Input value={form.secret_ref ?? ""} placeholder="secret://cliente/sistema" onChange={e => setForm({ ...form, secret_ref: e.target.value || null })} /></Label><div className="space-y-2"><Label>Permiso</Label><Select value={form.permission ?? "read"} onValueChange={value => setForm({ ...form, permission: value === "write" ? "write" : "read" })}><SelectTrigger aria-label="Permiso de la nueva conexión"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="read">Solo leer</SelectItem><SelectItem value="write">Leer y escribir</SelectItem></SelectContent></Select></div><fieldset className="md:col-span-2 space-y-2"><legend className="text-sm font-medium">Capacidades que respalda</legend>{capabilityCatalog.filter(c => c.needs_kinds.includes(form.kind)).map(cap => <Label key={cap.id} className="flex gap-2"><Checkbox checked={(form.backs ?? []).includes(cap.id)} onCheckedChange={value => setForm({ ...form, backs: value === true ? [...(form.backs ?? []), cap.id] : (form.backs ?? []).filter(c => c !== cap.id) })} />{cap.id}</Label>)}</fieldset><div className="md:col-span-2 space-y-3"><p className="text-sm font-medium">Ajustes extra</p>{Object.entries(form.settings ?? {}).filter(([key]) => !["driver", "url", "tool"].includes(key)).map(([key, value]) => <div key={key} className="flex gap-2 items-center"><span className="text-sm min-w-36">{key}</span><Input aria-label={`Ajuste ${key}`} value={value} onChange={e => settings(key, e.target.value)} /><Button type="button" variant="ghost" onClick={() => { const values = { ...form.settings }; delete values[key]; setForm({ ...form, settings: values }); }}>Quitar</Button></div>)}<div className="flex flex-wrap gap-2"><Input className="max-w-60" aria-label="Clave del ajuste" placeholder="Clave" value={extraKey} onChange={e => setExtraKey(e.target.value)} /><Input className="max-w-60" aria-label="Valor del ajuste" placeholder="Valor" value={extraValue} onChange={e => setExtraValue(e.target.value)} /><Button type="button" variant="outline" disabled={!extraKey.trim()} onClick={() => { settings(extraKey.trim(), extraValue); setExtraKey(""); setExtraValue(""); }}>Añadir ajuste</Button></div></div><p className="md:col-span-2 text-xs text-muted-foreground">ID: {id}. Usa referencias, no pegues contraseñas ni claves.</p><div className="flex gap-2"><Button type="submit">Incorporar conexión</Button><Button type="button" variant="ghost" onClick={close}>Cancelar</Button></div></form></CardContent></Card>;
}

export function ConnectionsEditor({ profile, edit }: EditorProps) {
  const { tenantId } = useTenant();
  const cache = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [site, setSite] = useState("");
  const [structures, setStructures] = useState<Record<string, string[]>>({});
  const live = useQuery({ queryKey: ["live", tenantId], refetchInterval: 5000, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/live", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  const sites = useQuery({ queryKey: ["sites", tenantId], enabled: profile.connections?.some(c => c.settings?.driver === "sim_sites") ?? false, queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/simulated/sites", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }});
  const inspect = useMutation({ mutationFn: async (id: string) => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/connections/{connection_id}/inspect", { params: { path: { tenant_id: tenantId, connection_id: id } } });
    if (!result.data) throw new Error(apiError(result.error)); return { id, data: result.data.structure };
  }, onSuccess: ({ id, data }) => setStructures(current => ({ ...current, [id]: data })) });
  const simulate = useMutation({ mutationFn: async () => {
    const result = await api.POST("/api/v1/tenants/{tenant_id}/simulated/open-site", { params: { path: { tenant_id: tenantId } }, body: { external_id: site } });
    if (!result.data) throw new Error(apiError(result.error)); return result.data;
  }, onSuccess: () => { void cache.invalidateQueries({ queryKey: ["live", tenantId] }); void cache.invalidateQueries({ queryKey: ["checks", tenantId] }); } });
  return <div className="space-y-4"><p className="text-sm text-muted-foreground">Añadir o quitar una conexión pide la aprobación del cliente al publicar.</p><Button variant="outline" onClick={() => setAdding(!adding)}>Añadir conexión</Button>{adding && <NewConnection profile={profile} edit={edit} close={() => setAdding(false)} />}{(inspect.error || simulate.error || live.error) && <p className="text-red-700" role="alert">{(inspect.error ?? simulate.error ?? live.error)?.message}</p>}{(profile.connections ?? []).map((connection, index) => {
    const status = live.data?.find(row => row.connection_id === connection.id);
    return <Card key={connection.id}><CardHeader><div className="flex flex-wrap gap-2 items-center"><CardTitle className="mr-auto">{connection.name}</CardTitle><Badge variant="secondary">{connection.kind}</Badge><Badge variant="outline">{connection.transport ?? "native"}</Badge><Button variant="ghost" onClick={() => edit(p => { p.connections?.splice(index, 1); })}>Quitar</Button></div><p className="text-sm text-muted-foreground">{connection.system}</p></CardHeader><CardContent className="space-y-4"><div className="grid gap-4 md:grid-cols-2"><Label>Referencia de credenciales<Input value={connection.secret_ref ?? ""} onChange={e => edit(p => { p.connections![index].secret_ref = e.target.value || null; })} /></Label><div className="space-y-2"><Label>Permiso</Label><Select value={connection.permission ?? "read"} onValueChange={value => edit(p => { p.connections![index].permission = value === "write" ? "write" : "read"; })}><SelectTrigger aria-label={`Permiso de ${connection.name}`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="read">Solo leer</SelectItem><SelectItem value="write">Leer y escribir</SelectItem></SelectContent></Select></div></div><div className="flex flex-wrap gap-4">{capabilityCatalog.filter(cap => cap.needs_kinds.includes(connection.kind)).map(cap => <Label key={cap.id} className="flex gap-2"><Checkbox checked={(connection.backs ?? []).includes(cap.id)} onCheckedChange={value => edit(p => { const c = p.connections![index]; c.backs = value === true ? [...(c.backs ?? []), cap.id] : (c.backs ?? []).filter(key => key !== cap.id); })} />{cap.id}</Label>)}</div><Button variant="outline" disabled={inspect.isPending} onClick={() => inspect.mutate(connection.id)}>Inspeccionar</Button>{structures[connection.id]?.map(line => <p className="text-sm" key={line}>{line}</p>)}<div className="rounded-md bg-muted/50 p-3"><p className="text-sm">{status?.summary ?? "Pendiente de comprobar"}</p>{status && <p className="mt-1 text-xs text-muted-foreground">Comprobado: {new Date(status.checked_at).toLocaleString("es")}</p>}{status?.warnings.map(w => <p key={w.code + w.text} className="mt-2 text-sm text-amber-800">{w.text}</p>)}</div></CardContent></Card>;
  })}{sites.data && <Card><CardHeader><CardTitle>Simular local nuevo sin agenda</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-3"><Select value={site} onValueChange={setSite}><SelectTrigger className="w-64" aria-label="Local de la simulación"><SelectValue placeholder="Elige un local" /></SelectTrigger><SelectContent>{sites.data.map(row => <SelectItem key={row.external_id} value={row.external_id}>{row.name}</SelectItem>)}</SelectContent></Select><Button variant="outline" disabled={!site || simulate.isPending} onClick={() => simulate.mutate()}>Simular local nuevo sin agenda</Button>{simulate.isSuccess && <p className="text-sm" role="status">Cambio guardado. Actualizando el estado vivo…</p>}</CardContent></Card>}</div>;
}
