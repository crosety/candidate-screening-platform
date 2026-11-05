import { test, expect } from "@playwright/test";
import { login } from "./helpers";
test("fuentes conserva procedencia y no inventa resultados sin documentos", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Biblioteca", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Biblioteca de documentos", exact: true })).toBeVisible();
  await page.getByText("Consultar el índice de una versión publicada", { exact: true }).click();
  await expect(page.getByLabel("Versión de fuentes")).toBeVisible();
  // Publishing another profile indexes its sources. Select the original seed's
  // deliberately empty index instead of depending on the latest version.
  await page.getByLabel("Versión de fuentes").selectOption({ label: "1.0 · 0 fragmentos" });
  await page.getByLabel("Pregunta a las fuentes").fill("¿Ofrecen transporte gratuito?");
  await page.getByRole("button", { name: "Buscar evidencia", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Sin evidencia relevante", exact: true })).toBeVisible();
});
