import { test, expect } from "@playwright/test";
import { login } from "./helpers";
test("mejora mantiene tareas pagadas desactivadas y permite revisar propuestas", async ({ page }) => {
  await login(page);
  await page.getByRole("link", { name: "Mejora continua", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Mejora supervisada", exact: true })).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "Evals nocturnas" })).not.toBeChecked();
  await expect(page.getByLabel("Muestra del juez")).toHaveValue("0");
  await page.getByRole("button", { name: "Generar propuestas", exact: true }).click();
  await expect(page.getByRole("button", { name: "Generar propuestas", exact: true })).toBeEnabled();
  await expect(page.getByRole("heading", { name: "No hay propuestas pendientes" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pilotos por segmento", exact: true })).toBeVisible();
});
