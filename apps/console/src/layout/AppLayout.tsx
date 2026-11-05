import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Navigate, Outlet, useLocation, useNavigate, useOutletContext, useParams } from "react-router";
import { Aperture, ArrowUpRight, BookOpen, BriefcaseBusiness, Building2, CalendarDays, ChartColumn, CheckCheck, ChevronRight, FlaskConical, GitBranch, Inbox, Layers, LogOut, Menu, MessageSquare, RefreshCw, Settings2, ShieldCheck, Sparkles, Users, X, type LucideIcon } from "lucide-react";
import { api, type components } from "@screening/api-client";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Avatar } from "@/components/WorkspaceUI";

type Tenant = components["schemas"]["TenantOut"];
type TenantContext = { tenantId: string; tenant: Tenant; tenants: Tenant[] };
type NavigationItem = { path: string; label: string; icon: LucideIcon };
const groups: { label: string; items: NavigationItem[] }[] = [
  { label: "Selección", items: [{ path: "bandeja", label: "Bandeja", icon: Inbox }, { path: "ofertas", label: "Ofertas", icon: BriefcaseBusiness }, { path: "candidaturas", label: "Candidaturas", icon: Users }, { path: "agenda", label: "Agenda", icon: CalendarDays }, { path: "analitica", label: "Analítica", icon: ChartColumn }] },
  { label: "Configuración FDE", items: [{ path: "perfil", label: "Empresa", icon: Settings2 }, { path: "fuentes", label: "Biblioteca", icon: BookOpen }, { path: "simulacion", label: "Simulación", icon: FlaskConical }, { path: "mejora", label: "Mejora continua", icon: Sparkles }] },
  { label: "Publicación", items: [{ path: "cambios", label: "Cambios", icon: GitBranch }, { path: "publicar", label: "Validar y publicar", icon: ArrowUpRight }, { path: "versiones", label: "Versiones", icon: Layers }] },
  { label: "Diagnóstico", items: [{ path: "conversaciones", label: "Trazas técnicas", icon: MessageSquare }, { path: "reenganche", label: "Recordatorios", icon: RefreshCw }] },
];
export function useTenant() { return useOutletContext<TenantContext>(); }

export function AppLayout() {
  const { tenantId = "" } = useParams();
  const [menuOpen, setMenuOpen] = useState(false);
  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [menuOpen]);
  const navigate = useNavigate();
  const location = useLocation();
  useEffect(() => {
    const page = groups.flatMap(group => group.items).find(item => location.pathname.split("/")[3] === item.path);
    document.title = `${page?.label ?? "Espacio de trabajo"} · Orbio`;
    window.scrollTo(0, 0);
  }, [location.pathname]);
  const cache = useQueryClient();
  const me = useQuery({ queryKey: ["me"], queryFn: async () => {
    const result = await api.GET("/api/v1/me");
    if (!result.data) throw new Error("Debes iniciar sesión", { cause: result.response.status });
    return result.data;
  }});
  const clients = useQuery({ queryKey: ["tenants"], queryFn: async () => {
    const result = await api.GET("/api/v1/tenants");
    if (!result.data) throw new Error("No se han podido cargar los clientes", { cause: result.response.status });
    return result.data;
  }});
  if (me.error instanceof Error && me.error.cause === 401) return <Navigate to="/login" replace />;
  if (me.isPending || clients.isPending) return <main className="p-8">Cargando…</main>;
  if (!me.data || !clients.data) return <main className="p-8" role="alert">No se ha podido cargar tu espacio.</main>;
  const tenant = clients.data.find(t => t.id === tenantId);
  if (!tenant) return <main className="p-8">Cliente no encontrado.</main>;
  async function logout() {
    const result = await api.POST("/api/v1/auth/logout");
    if (result.response.ok) { cache.clear(); navigate("/login"); }
  }
  const visibleGroups = tenant.role === "recruiter" ? [groups[0]!] : groups;
  const current = groups.flatMap(g => g.items).find(item => location.pathname.split("/")[3] === item.path);
  return <div className="workspace-shell">
    <a className="skip-link" href="#workspace-content">Ir al contenido</a>
    {menuOpen && <button className="sidebar-scrim" aria-label="Cerrar navegación" onClick={() => setMenuOpen(false)} />}
    <aside id="workspace-sidebar" className={`workspace-sidebar ${menuOpen ? "is-open" : ""}`}>
      <div className="workspace-brand"><span className="brand-mark"><Aperture size={23} strokeWidth={1.6} aria-hidden="true" /></span><div><span className="brand-name">orbio<span className="brand-dot">.</span></span><span className="brand-subtitle">Espacio de selección</span></div><button className="mobile-close" aria-label="Cerrar menú" onClick={() => setMenuOpen(false)}><X size={18} /></button></div>
      <div className="tenant-switcher"><p className="eyebrow">Espacio de trabajo</p><Select value={tenantId} onValueChange={id => { navigate(`/t/${id}/bandeja`); setMenuOpen(false); }}><SelectTrigger aria-label="Cliente" className="w-full h-11 bg-white"><span className="flex items-center gap-2 min-w-0"><Building2 size={15} className="text-muted-foreground shrink-0" /><SelectValue /></span></SelectTrigger><SelectContent>{clients.data.map(t => <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>)}</SelectContent></Select></div>
      <nav className="workspace-nav" aria-label="Navegación del cliente">{visibleGroups.map(group => <div className="nav-group" key={group.label}><p className="nav-group-label">{group.label}</p>{group.items.map(({ path, label, icon: Icon }) => <NavLink key={path} to={`/t/${tenantId}/${path}`} onClick={() => setMenuOpen(false)} className={({ isActive }) => `workspace-nav-link ${isActive ? "is-active" : ""}`}><Icon size={17} strokeWidth={1.65} aria-hidden="true" /><span>{label}</span></NavLink>)}</div>)}</nav>
      <div className="sidebar-bottom"><div className="workspace-context"><ShieldCheck size={15} aria-hidden="true" /><span>Espacio privado</span><span className="local-tag">Local</span></div><div className="user-account"><Avatar name={me.data.full_name} small /><div><strong>{me.data.full_name}</strong><span>{tenant.role === "recruiter" ? "Equipo de selección" : "Forward Deployed Engineer"}</span></div><Button size="icon" variant="ghost" aria-label="Salir" title="Cerrar sesión" onClick={logout}><LogOut size={16} /></Button></div></div>
    </aside>
    <div className="workspace-body"><header className="workspace-topbar"><div className="workspace-breadcrumb"><Button className="mobile-menu" size="icon" variant="ghost" aria-label="Abrir menú" aria-expanded={menuOpen} aria-controls="workspace-sidebar" onClick={() => setMenuOpen(true)}><Menu /></Button><span>{tenant.name}</span><ChevronRight size={13} aria-hidden="true" /><strong>{current?.label ?? "Espacio de trabajo"}</strong></div>{tenant.role !== "recruiter" && <div className="workspace-version"><span className="version-indicator" /><span>{tenant.active_version ? `Versión ${tenant.active_version}` : "Sin publicar"}</span><span className="draft-indicator">Borrador · rev {tenant.draft_rev}</span></div>}</header>
      <main id="workspace-content" className="workspace-content"><div className="workspace-page"><Outlet key={tenantId} context={{ tenantId, tenant, tenants: clients.data } satisfies TenantContext} /></div><footer className="workspace-footer"><span>Selección con criterio. Decisiones con evidencia.</span><span><CheckCheck size={13} aria-hidden="true" /> {tenant.role === "recruiter" ? "Acceso de selección" : "Consola del FDE"}</span></footer></main>
    </div>
  </div>;
}
