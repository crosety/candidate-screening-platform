import { useQuery } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import type { EditorProps } from "@/components/ProfileEditors";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { apiError } from "@/lib/errors";

export function ChannelSettingsEditor({ profile, edit, tenantId }: EditorProps & { tenantId: string }) {
  const status = useQuery({ queryKey: ["channels-status", tenantId], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants/{tenant_id}/channels/status", { params: { path: { tenant_id: tenantId } } });
    if (!result.data) throw new Error(apiError(result.error));
    return result.data;
  }});
  const whatsapp = profile.channel_settings?.whatsapp;
  function changeWhatsApp(change: (value: NonNullable<NonNullable<typeof profile.channel_settings>["whatsapp"]>) => void) {
    edit(p => {
      p.channel_settings ??= {};
      p.channel_settings.whatsapp ??= { first_contact: "wait", templates: [] };
      change(p.channel_settings.whatsapp);
    });
  }
  return <div className="space-y-5">
    <Card><CardHeader><CardTitle>Canales habilitados</CardTitle></CardHeader><CardContent className="flex flex-wrap gap-6">{(["web", "whatsapp", "voice"] as const).map(channel => <Label key={channel} className="flex items-center gap-2"><Checkbox checked={profile.channels.includes(channel)} onCheckedChange={checked => edit(p => { p.channels = checked === true ? [...new Set([...p.channels, channel])] : p.channels.filter(value => value !== channel); })} />{channel === "voice" ? "Voz en navegador" : channel === "web" ? "Chat web" : "WhatsApp"}</Label>)}</CardContent></Card>
    <Card><CardHeader><div className="flex items-center justify-between"><CardTitle>WhatsApp · Meta Cloud API</CardTitle><Badge variant="secondary">{status.data?.whatsapp_configured ? "Configuración presente" : "Pendiente de configurar"}</Badge></div></CardHeader><CardContent className="space-y-5">
      <p className="text-sm text-muted-foreground">{status.data?.detail ?? status.error?.message ?? "Comprobando configuración publicada…"}</p>
      <Label className="block">Referencia del emisor<Input placeholder="secret://cliente/whatsapp" value={whatsapp?.sender_ref ?? ""} onChange={e => changeWhatsApp(value => { value.sender_ref = e.target.value || null; })} /></Label>
      <p className="text-xs text-muted-foreground">Solo referencias: no pegues tokens, claves ni contraseñas. La configuración del emisor se resuelve en el servidor y nunca se muestra aquí.</p>
      <Label className="block">Primer contacto<select className="mt-2 block rounded border bg-background p-2" value={whatsapp?.first_contact ?? "wait"} onChange={e => changeWhatsApp(value => { value.first_contact = e.target.value as "wait" | "template"; })}><option value="wait">Esperar a que escriba el candidato</option><option value="template">Plantilla aprobada de primer contacto</option></select></Label>
      <div className="space-y-3"><p className="font-medium">Referencias de plantillas</p>{whatsapp?.templates.map((item, index) => <div key={index} className="grid gap-2 rounded border p-3 md:grid-cols-[1fr_100px_160px_auto]">
        <Input aria-label={`Nombre plantilla ${index + 1}`} placeholder="Nombre en Meta" value={item.name} onChange={e => changeWhatsApp(value => { value.templates[index].name = e.target.value; })} />
        <Input aria-label={`Idioma plantilla ${index + 1}`} value={item.language} onChange={e => changeWhatsApp(value => { value.templates[index].language = e.target.value; })} />
        <select aria-label={`Propósito plantilla ${index + 1}`} className="rounded border bg-background px-2 text-sm" value={item.purpose} onChange={e => changeWhatsApp(value => { value.templates[index].purpose = e.target.value as "first_contact" | "followup"; })}><option value="first_contact">Primer contacto</option><option value="followup">Reenganche</option></select>
        <Button variant="ghost" onClick={() => changeWhatsApp(value => { value.templates.splice(index, 1); })}>Quitar</Button>
      </div>)}<Button variant="outline" onClick={() => changeWhatsApp(value => { value.templates.push({ name: "", language: "es", purpose: "followup", variables: [] }); })}>Añadir referencia de plantilla</Button></div>
      <p className="text-xs text-muted-foreground">Meta comprueba la aprobación real al enviar. Referenciar una plantilla en este formulario no equivale a aprobarla.</p>
      {status.data && <div className="rounded bg-muted p-3 text-xs space-y-2"><p>Webhook privado de integración: <code>{status.data.webhook_path}</code></p><p>Envíos externos: {status.data.external_sends_enabled ? "habilitados" : "desactivados"} · Incidencias de entrada: {status.data.failed_jobs}</p></div>}
    </CardContent></Card>
    <Card><CardHeader><CardTitle>Voz · ElevenLabs privado</CardTitle></CardHeader><CardContent className="space-y-4"><p className="text-sm text-muted-foreground">Requiere agente privado con Custom LLM y callback propio. Esta configuración no acredita una prueba de audio real.</p><Label className="block">Referencia del agente de voz<Input placeholder="secret://cliente/voz" value={profile.channel_settings?.voice?.agent_ref ?? ""} onChange={e => edit(p => { p.channel_settings ??= {}; p.channel_settings.voice ??= { agent_ref: "", voice_id: "" }; p.channel_settings.voice.agent_ref = e.target.value; })} /></Label><Label className="block">Identificador de voz<Input value={profile.channel_settings?.voice?.voice_id ?? ""} onChange={e => edit(p => { p.channel_settings ??= {}; p.channel_settings.voice ??= { agent_ref: "", voice_id: "" }; p.channel_settings.voice.voice_id = e.target.value; })} /></Label><p className="text-xs text-muted-foreground">Solo referencias e identificadores públicos; las claves y el secreto de callback permanecen en el servidor. No compartimos la clave de OpenAI con ElevenLabs.</p></CardContent></Card>
    <p className="text-sm text-muted-foreground">Estos cambios se guardan en el borrador y pasan por diff, evals y aprobaciones antes de activarse. El teléfono simulado no envía a Meta.</p>
  </div>;
}
