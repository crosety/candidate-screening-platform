import { test, expect } from "@playwright/test";
import { login } from "./helpers";
test("la reclutadora opera Bandeja, Ofertas y Candidaturas sin ajustes FDE", async ({ page }) => {
  await login(page, "reclutadora@orbio.local");
  const links = page.getByRole("navigation").getByRole("link");
  await expect(links).toHaveCount(5);
  await expect(links).toHaveText(["Bandeja", "Ofertas", "Candidaturas", "Agenda", "Analítica"]);
  await page.getByRole("link", { name: "Ofertas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Ofertas", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Crear oferta", exact: true })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Configurar", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "Candidaturas", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Candidaturas", exact: true })).toBeVisible();
});
