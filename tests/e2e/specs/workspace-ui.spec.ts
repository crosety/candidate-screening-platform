import { test, expect } from "@playwright/test";
import { createChat, login, selectTenant } from "./helpers";

test("selección filtra sin perder el contexto y permite limpiar", async ({ page }) => {
  await login(page, "reclutadora@orbio.local");
  await page.getByRole("navigation").getByRole("link", { name: "Candidaturas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Candidaturas", exact: true })).toBeVisible();
  await page.getByLabel("Filtrar por estado", { exact: true }).selectOption("completed");
  await page.getByLabel("Buscar candidato", { exact: true }).fill("segmento-inexistente-ui-test");
  await expect(page.getByRole("heading", { name: "No hay coincidencias" })).toBeVisible();
  await page.getByRole("button", { name: "Limpiar filtros", exact: true }).click();
  await expect(page.getByLabel("Filtrar por estado", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Buscar candidato", { exact: true })).toHaveValue("");
  await expect(page.getByRole("button", { name: "Limpiar filtros", exact: true })).toHaveCount(0);
});

test("el FDE filtra conversaciones y puede restaurar el listado", async ({ page }) => {
  await login(page);
  await selectTenant(page, "Grupo Sazón");
  const chat = await createChat(page, `Listado UI ${Date.now()}`);
  await chat.close();
  await page.getByRole("link", { name: "Trazas técnicas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Registro de conversaciones", exact: false })).toBeVisible();
  await page.getByLabel("Buscar conversación").fill("sin-coincidencias-ui-test");
  await expect(page.getByRole("heading", { name: "No hay coincidencias" })).toBeVisible();
  await page.getByLabel("Estado de conversación").selectOption("completed");
  await page.getByRole("button", { name: "Limpiar", exact: true }).click();
  await expect(page.getByLabel("Buscar conversación")).toHaveValue("");
  await expect(page.getByLabel("Estado de conversación")).toHaveValue("");
  await expect(page.getByRole("button", { name: "Limpiar", exact: true })).toHaveCount(0);
});

test("perfil móvil separa el copiloto y descarta cambios sin guardarlos", async ({ page }) => {
  await login(page);
  await selectTenant(page, "Grupo Sazón");
  await page.getByRole("link", { name: "Empresa", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const field = page.getByLabel("El puesto y el cliente", { exact: true });
  const original = await field.inputValue();
  await expect(page.getByRole("button", { name: "Guardar borrador", exact: true })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Sugerencias del copiloto", exact: true })).not.toBeVisible();
  await field.fill(`${original}\nCambio local de prueba.`);
  await expect(page.getByRole("button", { name: "Guardar borrador", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "Descartar cambios", exact: true }).click();
  await expect(field).toHaveValue(original);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test("el copiloto no traslada sugerencias entre secciones de Empresa", async ({ page }) => {
  await login(page);
  await selectTenant(page, "Grupo Sazón");
  await page.getByRole("link", { name: "Empresa", exact: true }).click();
  await page.route("**/copilot/suggestions", route => route.fulfill({ json: [{ field: "voice", append: "Una sola pregunta por turno.", why: "Claridad", evidence_ids: [] }] }));
  await page.getByText("Copiloto del FDE", { exact: true }).click();
  await page.getByRole("button", { name: "Sugerencias del copiloto", exact: true }).click();
  await expect(page.getByRole("button", { name: "Añadir al borrador", exact: true })).toBeVisible();
  await page.getByRole("tab", { name: "Instrucciones comunes", exact: true }).click();
  await page.getByText("Copiloto del FDE", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Añadir al borrador", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Guardar borrador", exact: true })).toBeDisabled();
});

test("navegación móvil se cierra al cambiar de página y no desborda", async ({ page }) => {
  await login(page, "reclutadora@orbio.local");
  await page.setViewportSize({ width: 390, height: 844 });
  const menu = page.getByRole("button", { name: "Abrir menú", exact: true });
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await menu.click();
  await expect(menu).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("navigation").getByRole("link", { name: "Candidaturas", exact: true }).click();
  await expect(menu).toHaveAttribute("aria-expanded", "false");
  await expect(page.getByRole("heading", { name: "Candidaturas", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
