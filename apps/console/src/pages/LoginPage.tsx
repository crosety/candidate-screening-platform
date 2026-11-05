import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@screening/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Aperture, ArrowRight, Check, ShieldCheck } from "lucide-react";

export function LoginPage() {
  useEffect(() => { document.title = "Iniciar sesión · Orbio"; }, []);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const navigate = useNavigate();
  const cache = useQueryClient();
  async function submit(event: FormEvent) {
    event.preventDefault(); setPending(true); setError("");
    try {
      const result = await api.POST("/api/v1/auth/login", { body: { email, password } });
      if (!result.response.ok) {
        const detail = result.error as { detail?: string } | undefined;
        setError(detail?.detail ?? "No se ha podido iniciar sesión."); return;
      }
      cache.clear(); navigate("/");
    } catch { setError("No se ha podido conectar con el servidor."); }
    finally { setPending(false); }
  }
  return <main className="login-layout"><section className="login-story"><div className="login-brand"><Aperture size={26} strokeWidth={1.5} /><span>orbio.</span></div><div className="login-message"><p className="eyebrow">Screening workspace</p><h1>Conversaciones que<br />abren posibilidades.</h1><p>Un espacio para diseñar el cribado, entender cada candidatura y decidir con evidencia.</p><div className="login-principles"><span><Check size={14} /> Cada cliente, su contexto</span><span><Check size={14} /> Cada decisión, su evidencia</span><span><Check size={14} /> El equipo siempre al mando</span></div></div><div className="login-story-footer"><ShieldCheck size={14} /> Espacio de trabajo privado</div></section><section className="login-form-panel">
    <Card className="login-card">
      <CardHeader><p className="eyebrow">Bienvenido a tu espacio</p><CardTitle className="text-2xl">Iniciar sesión</CardTitle><CardDescription>Accede a tus clientes y procesos de selección.</CardDescription></CardHeader>
      <CardContent><form onSubmit={submit} className="space-y-5">
        <div className="space-y-2"><Label htmlFor="email">Email</Label><Input id="email" type="email" autoComplete="username" placeholder="tu@empresa.com" required value={email} onChange={e => setEmail(e.target.value)} /></div>
        <div className="space-y-2"><Label htmlFor="password">Contraseña</Label><Input id="password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /></div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button className="w-full" type="submit" disabled={pending}>{pending ? "Entrando…" : "Entrar"}<ArrowRight size={15} /></Button>
      </form></CardContent>
    </Card><p className="login-security-note"><ShieldCheck size={13} /> Acceso seguro con los permisos de tu equipo.</p></section>
  </main>;
}
