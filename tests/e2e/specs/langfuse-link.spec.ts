import { createHash } from "node:crypto";
import { expect, test } from "@playwright/test";
import { createChat, login, selectTenant } from "./helpers";

test("el FDE abre las métricas de la misma sesión sin compartir el origen", async ({ page, context }, testInfo) => {
  await login(page);
  await selectTenant(page, "Grupo Sazón");
  const name = `Métricas UI ${Date.now()}`;
  const chat = await createChat(page, name);
  await chat.close();
  await page.getByRole("link", { name: "Trazas técnicas", exact: true }).click();
  await page.getByLabel("Buscar conversación").fill(name);
  await page.getByRole("link", { name: `Abrir conversación de ${name}`, exact: true }).click();
  const link = page.getByRole("link", { name: "Abrir en Langfuse" });
  await expect(link).toBeVisible();
  const path = new URL(page.url()).pathname.split("/");
  const id = createHash("sha256").update(`${path[2]}:${path[4]}`).digest("hex");
  const target = `https://cloud.langfuse.com/project/e2e-metrics/sessions/${id}`;
  await expect(link).toHaveAttribute("href", target);
  await expect(link).toHaveAttribute("target", "_blank");
  await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await expect(link).toHaveAttribute("referrerpolicy", "no-referrer");
  await page.screenshot({ path: testInfo.outputPath("langfuse-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(link).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("langfuse-mobile.png"), fullPage: true });
  // Only the external destination is replaced; Orbio's UI and API run unchanged.
  await context.route("https://cloud.langfuse.com/**", route => {
    expect(route.request().headers().referer).toBeUndefined();
    return route.fulfill({ contentType: "text/html; charset=utf-8", body: "<h1>Sesión de métricas</h1>" });
  });
  const opened = context.waitForEvent("page");
  await link.click();
  const destination = await opened;
  await expect(destination).toHaveURL(target);
  await expect(destination.getByRole("heading", { name: "Sesión de métricas" })).toBeVisible();
  expect(await destination.evaluate(() => window.opener === null)).toBe(true);
  await destination.close();
});
