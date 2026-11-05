import { StrictMode, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createBrowserRouter, RouterProvider, Navigate, Route, Routes } from "react-router";
import { api } from "@screening/api-client";
import { LoginPage } from "./pages/LoginPage";
import { HomePage } from "./pages/HomePage";
import { AppLayout } from "./layout/AppLayout";
import { ProfilePage } from "./pages/ProfilePage";
import { ChangesPage } from "./pages/ChangesPage";
import { PublishPage } from "./pages/PublishPage";
import { VersionsPage } from "./pages/VersionsPage";
import { SessionsPage } from "./pages/SessionsPage";
import { SessionDetailPage } from "./pages/SessionDetailPage";
import { AtsPage } from "./pages/AtsPage";
import { WorkspacePage } from "./pages/WorkspacePage";
import { CandidatePage } from "./pages/CandidatePage";
import { OffersPage, OfferEditorPage } from "./pages/OffersPage";
import { SimulationPage } from "./pages/SimulationPage";
import { EvalsPage } from "./pages/EvalsPage";
import { EvalDetailPage } from "./pages/EvalDetailPage";
import { PhonePage } from "./pages/PhonePage";
import { FollowupsPage } from "./pages/FollowupsPage";
import { ImprovementsPage } from "./pages/ImprovementsPage";
import { FaqPage } from "./pages/FaqPage";
import { AnalyticsPage } from "./pages/AnalyticsPage";
import { AgendaPage } from "./pages/AgendaPage";
import "./index.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });

function App() {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    api.GET("/api/v1/auth/csrf").then(({ response }) => {
      if (response.ok) setReady(true);
      else setError("No se ha podido conectar con el servidor.");
    }).catch(() => setError("No se ha podido conectar con el servidor."));
  }, []);
  if (error) return <main className="p-8" role="alert">{error}</main>;
  if (!ready) return <main className="p-8">Conectando…</main>;
  return <Routes>
    <Route path="/login" element={<LoginPage />} />
    <Route path="/" element={<HomePage />} />
    <Route path="/t/:tenantId" element={<AppLayout />}>
      <Route index element={<Navigate to="bandeja" replace />} />
      <Route path="bandeja" element={<WorkspacePage inbox />} />
      <Route path="candidaturas" element={<WorkspacePage />} />
      <Route path="candidaturas/:sessionId" element={<CandidatePage />} />
      <Route path="agenda" element={<AgendaPage />} />
      <Route path="ofertas" element={<OffersPage />} />
      <Route path="ofertas/crear/nueva" element={<OfferEditorPage />} />
      <Route path="ofertas/:offerKey" element={<OfferEditorPage />} />
      <Route path="simulacion" element={<SimulationPage />} />
      <Route path="seleccion" element={<Navigate to="../candidaturas" replace />} />
      <Route path="analitica" element={<AnalyticsPage />} />
      <Route path="mejora" element={<ImprovementsPage />} />
      <Route path="fuentes" element={<FaqPage />} />
      <Route path="evals" element={<EvalsPage />} />
      <Route path="idiomas" element={<div className="space-y-4"><p className="rounded-lg border p-4 text-sm">Suite ES / EN / mixta. Mismos hechos y veredictos; solo cambia el idioma. No sustituye la suite base de publicación.</p><EvalsPage languages /></div>} />
      <Route path="evals/:runId" element={<EvalDetailPage />} />
      <Route path="perfil" element={<ProfilePage />} />
      <Route path="cambios" element={<ChangesPage />} />
      <Route path="publicar" element={<PublishPage />} />
      <Route path="versiones" element={<VersionsPage />} />
      <Route path="conversaciones" element={<SessionsPage />} />
      <Route path="conversaciones/:sessionId" element={<SessionDetailPage />} />
      <Route path="ats" element={<AtsPage />} />
      <Route path="telefono" element={<PhonePage />} />
      <Route path="reenganche" element={<FollowupsPage />} />
    </Route>
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>;
}

const root: Root = import.meta.hot?.data.root ?? createRoot(document.getElementById("root")!);
if (import.meta.hot) import.meta.hot.data.root = root;
const router = createBrowserRouter([{ path: "*", element: <App /> }]);
root.render(
  <StrictMode><QueryClientProvider client={queryClient}><RouterProvider router={router} /></QueryClientProvider></StrictMode>,
);
