import { test, expect, type Page } from "@playwright/test";

const tenantId = "00000000-0000-4000-8000-000000000011";
const offer = (key: string, status: string, published: boolean) => ({
  key, title: `Oferta ${key}`, status, description: "Oferta de prueba.", dimensions: {},
  applications: 0, attention: 0, configured: true, has_changes: !published,
  published: published ? { key, title: `Oferta ${key}`, status } : null,
  draft: null, application_url: status === "open" ? "http://localhost:15174/apply/ui/open" : null,
});

async function openSimulation(page: Page, offers: ReturnType<typeof offer>[], failOffers: boolean | "network" = false, waitForOffers?: Promise<void>) {
  const writes: string[] = [];
  await page.route("**/api/v1/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (request.method() !== "GET") {
      writes.push(path);
      return route.fulfill({ status: 400, json: { detail: "No se permiten escrituras en esta prueba." } });
    }
    if (path === "/api/v1/auth/csrf") return route.fulfill({ json: {} });
    if (path === "/api/v1/me") return route.fulfill({ json: { id: "fde-ui", full_name: "FDE", memberships: [] } });
    if (path === "/api/v1/tenants") return route.fulfill({ json: [{ id: tenantId, name: "Cliente de prueba", role: "fde", active_version: "1", draft_rev: 1 }] });
    if (path.endsWith("/simulated/ats")) return route.fulfill({ json: [] });
    if (path.endsWith("/offers")) {
      if (waitForOffers) await waitForOffers;
      if (failOffers === "network") return route.abort();
      return failOffers
        ? route.fulfill({ status: 503, json: { detail: "No se pueden cargar las ofertas." } })
        : route.fulfill({ json: { offers, rev: 1, shared_fields: [], allowed_capabilities: [] } });
    }
    return route.fulfill({ status: 404, json: { detail: "Ruta fuera de esta prueba." } });
  });
  await page.goto(`/t/${tenantId}/simulacion?tab=candidatos`);
  await expect(page.getByText("Nueva candidatura de prueba", { exact: true })).toBeVisible();
  return writes;
}

test("una prueba solo permite elegir ofertas publicadas y abiertas", async ({ page }) => {
  const writes = await openSimulation(page, [offer("abierta", "open", true), offer("pausada", "paused", true), offer("borrador", "draft", false)]);
  const selector = page.getByRole("combobox", { name: "Oferta publicada" });
  await expect(selector.getByRole("option")).toHaveText(["Elige una oferta", "Oferta abierta"]);
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
  await selector.selectOption("abierta");
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeEnabled();
  await expect(page.getByRole("textbox", { name: "Puesto", exact: true })).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("sin oferta publicada abierta no se genera un enlace inutilizable", async ({ page }) => {
  const writes = await openSimulation(page, [offer("cerrada", "closed", true), offer("borrador", "draft", false)]);
  await expect(page.getByText("Publica y abre una oferta antes de crear una candidatura de prueba.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
  expect(writes).toEqual([]);
});

test("si la oferta cambia al crear, conserva los datos y actualiza el selector", async ({ page }) => {
  const offers = [offer("abierta", "open", true)];
  await openSimulation(page, offers);
  await page.route("**/simulated/ats", route => {
    if (route.request().method() !== "POST") return route.fallback();
    offers[0] = offer("abierta", "paused", true);
    return route.fulfill({ status: 409, json: { detail: "Elige una oferta publicada y abierta antes de crear la prueba." } });
  });
  await page.getByLabel("Nombre", { exact: true }).fill("Persona de prueba");
  await page.getByLabel("Ciudad", { exact: true }).fill("Valencia");
  await page.getByRole("combobox", { name: "Oferta publicada" }).selectOption("abierta");
  await page.getByRole("button", { name: "Crear candidatura", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Elige una oferta publicada y abierta");
  await expect(page.getByLabel("Nombre", { exact: true })).toHaveValue("Persona de prueba");
  await expect(page.getByLabel("Ciudad", { exact: true })).toHaveValue("Valencia");
  await expect(page.getByRole("combobox", { name: "Oferta publicada" }).getByRole("option")).toHaveCount(1);
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
  await expect(page.getByRole("status").getByRole("link")).toHaveCount(0);
});

test("un error al consultar ofertas no permite crear una prueba sin contexto", async ({ page }) => {
  const writes = await openSimulation(page, [], true);
  await expect(page.getByRole("alert")).toContainText("No se pueden cargar las ofertas.");
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
  expect(writes).toEqual([]);
  await page.route("**/offers", route => route.fulfill({ json: { offers: [offer("abierta", "open", true)], rev: 1, shared_fields: [], allowed_capabilities: [] } }));
  await page.getByRole("button", { name: "Reintentar ofertas", exact: true }).click();
  await expect(page.getByRole("combobox", { name: "Oferta publicada" })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(writes).toEqual([]);
});

test("la carga de ofertas explica por qué aún no se puede crear", async ({ page }) => {
  let release = () => {};
  const pending = new Promise<void>(resolve => { release = resolve; });
  try {
    await openSimulation(page, [offer("abierta", "open", true)], false, pending);
    await expect(page.getByRole("status")).toHaveText("Cargando ofertas…");
    await expect(page.getByRole("textbox", { name: "Puesto", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
  } finally { release(); }
  await expect(page.getByRole("combobox", { name: "Oferta publicada" })).toBeVisible();
});

test("un fallo de red ofrece recuperación en español sin errores técnicos", async ({ page }) => {
  await openSimulation(page, [], "network");
  await expect(page.getByRole("alert")).toContainText("No se pueden cargar las ofertas.");
  await expect(page.getByRole("alert")).not.toContainText(/Failed to fetch|NetworkError|TypeError/);
  await expect(page.getByRole("button", { name: "Reintentar ofertas", exact: true })).toBeEnabled();
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeDisabled();
});

test("un cliente sin ofertas conserva el puesto de su configuración común", async ({ page }) => {
  const writes = await openSimulation(page, []);
  await page.getByRole("textbox", { name: "Puesto", exact: true }).fill("Puesto del cliente");
  await expect(page.getByRole("button", { name: "Crear candidatura", exact: true })).toBeEnabled();
  expect(writes).toEqual([]);
});
