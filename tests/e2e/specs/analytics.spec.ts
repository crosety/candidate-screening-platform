import { test, expect, type Page } from "@playwright/test";
import { createChat, login } from "./helpers";

async function openAnalytics(page: Page) {
  await page.getByRole("navigation").getByRole("link", { name: "Analítica", exact: true }).click();
  await expect(page).toHaveURL(/\/t\/[^/]+\/analitica$/);
  await expect(page.getByRole("heading", { name: "Analítica", exact: true })).toBeVisible();
}

test("la reclutadora llega a Analítica desde Selección y ve definiciones explícitas", async ({ page }) => {
  await login(page, "reclutadora@orbio.local");
  await openAnalytics(page);
  await expect(page.getByText("las conversaciones en curso no cuentan en el denominador", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Observación de sentimiento (no es criterio de selección)", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Etapa en la que se abandona", exact: true })).toBeVisible();
  // Operation only: the simulation switch is an FDE tool.
  await expect(page.getByLabel("Datos", { exact: true })).toHaveCount(0);
  const funnel = page.getByRole("region", { name: "Embudo de conversación" });
  await funnel.getByRole("button", { name: "Ver tabla" }).click();
  await expect(funnel.getByRole("columnheader", { name: "Sobre iniciadas" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Analítica", exact: true })).toBeVisible();
});

test("el FDE ve Analítica en Selección y puede separar simulaciones", async ({ page }) => {
  await login(page);
  await openAnalytics(page);
  await page.getByLabel("Datos", { exact: true }).selectOption("simulation");
  await expect(page.getByText("Solo simulaciones del FDE", { exact: false })).toBeAttached();
});

test("un fallo de analítica no se presenta como cero y permite recuperarse", async ({ page }) => {
  await login(page);
  let failed = true;
  await page.route("**/analytics?**", route => failed
    ? route.fulfill({ status: 503, json: { detail: "Unavailable" } }) : route.continue());
  await openAnalytics(page);
  await expect(page.getByRole("alert").filter({ hasText: "No se puede cargar la analítica" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Resultados de las conversaciones" })).toHaveCount(0);
  await expect(page.getByText("No hay abandonos en este filtro.")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "Reintentar analítica", exact: true }).click();
  await expect(page.getByRole("region", { name: "Resultados de las conversaciones" })).toBeVisible();
});

test("el candidato ve el fallo de consulta de entrevista y puede reintentar", async ({ page, context }) => {
  await login(page);
  let failed = true;
  await context.route("**/api/v1/chat/interview", route => failed
    ? route.fulfill({ status: 503, json: { detail: "Unavailable" } }) : route.continue());
  const chat = await createChat(page, "Consulta de entrevista");
  await expect(chat.getByRole("alert").filter({ hasText: "No hemos podido comprobar tu entrevista." })).toBeVisible();
  failed = false;
  await chat.getByRole("button", { name: "Reintentar entrevista", exact: true }).click();
  await expect(chat.getByRole("alert").filter({ hasText: "No hemos podido comprobar tu entrevista." })).toHaveCount(0);
});
