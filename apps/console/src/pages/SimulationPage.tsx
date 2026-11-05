import { useSearchParams } from "react-router";
import { PageHeader } from "@/components/WorkspaceUI";
import { useTenant } from "@/layout/AppLayout";
import { AtsPage } from "./AtsPage";
import { PhonePage } from "./PhonePage";
import { EvalsPage } from "./EvalsPage";
import { WorkspacePage } from "./WorkspacePage";
import { ProfilePage } from "./ProfilePage";

const tabs = [{ key: "conversaciones", label: "Conversaciones" }, { key: "candidatos", label: "Crear prueba · texto y voz" }, { key: "telefono", label: "Mensajería" }, { key: "evaluaciones", label: "Evaluaciones" }, { key: "idiomas", label: "Idiomas" }, { key: "quality", label: "Calidad del agente" }];
export function SimulationPage() {
  const { tenant } = useTenant();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") ?? "conversaciones";
  if (tenant.role === "recruiter") return <p role="alert">Las simulaciones se preparan desde la consola FDE.</p>;
  return <div className="space-y-6 simulation-page"><PageHeader eyebrow="Configuración FDE" title="Simulación" description="Prueba la experiencia con candidaturas sintéticas, muestra el recorrido al cliente y valida los cambios antes de publicarlos." /><div className="simulation-notice"><span>Entorno de pruebas</span> Las conversaciones, registros ATS y reservas simuladas no se mezclan con la actividad de selección. Las pruebas que usan modelos o voz pueden consumir créditos.</div><nav className="workspace-tabs" aria-label="Modos de simulación">{tabs.map(item => <button key={item.key} className={tab === item.key ? "active" : ""} aria-current={tab === item.key ? "page" : undefined} onClick={() => setParams({ tab: item.key })}>{item.label}</button>)}</nav><div className="simulation-content" key={tab}>{tab === "quality" ? <ProfilePage mode="quality" /> : tab === "candidatos" ? <AtsPage /> : tab === "telefono" ? <PhonePage /> : tab === "evaluaciones" ? <EvalsPage /> : tab === "idiomas" ? <EvalsPage languages /> : <WorkspacePage simulation />}</div></div>;
}
