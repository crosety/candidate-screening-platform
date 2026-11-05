import { useQuery } from "@tanstack/react-query";
import { Navigate } from "react-router";
import { api } from "@screening/api-client";

export function HomePage() {
  const clients = useQuery({ queryKey: ["tenants"], queryFn: async () => {
    const { data, response } = await api.GET("/api/v1/tenants");
    if (!response.ok || !data) throw new Error("No se han podido cargar los clientes.", { cause: response.status });
    return data;
  }});
  if (clients.error instanceof Error && clients.error.cause === 401) return <Navigate to="/login" replace />;
  if (clients.isPending) return <main className="p-8">Cargando…</main>;
  if (clients.isError) return <main className="p-8" role="alert">{clients.error.message}</main>;
  if (!clients.data.length) return <main className="p-8">No tienes clientes asignados.</main>;
  return <Navigate to={`/t/${clients.data[0].id}/bandeja`} replace />;
}
