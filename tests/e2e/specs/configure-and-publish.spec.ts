import { test, expect } from "@playwright/test";
import { login, selectTenant } from "./helpers";
test("edita, revisa y publica una versión", async ({ page }) => {
  await login(page); await selectTenant(page, "Grupo Sazón");
  await page.getByRole("link", { name: "Empresa", exact: true }).click();
  const field = page.getByLabel("Encaje con el trabajo", { exact: true });
  const text = `Prueba e2e ${Date.now()}.`;
  await field.fill(`${await field.inputValue()}\n${text}`);
  await page.getByRole("button", { name: "Guardar borrador" }).click();
  await expect(page.getByText("Borrador guardado.", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Cambios", exact: true }).click();
  await expect(page.getByText(text, { exact: false }).first()).toBeVisible();
  await page.getByRole("link", { name: "Validar y publicar", exact: true }).click();
  await page.getByLabel("Nota de la versión").fill("e2e");
  await page.getByRole("button", { name: "Publicar", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText(/Activa al 100 %|Pendiente de aprobación/);
  if ((await page.getByRole("status").textContent()) === "Pendiente de aprobación") {
    const publicationUrl = page.url();
    await page.getByRole("button", { name: "Salir", exact: true }).click();
    await login(page, "fde2@orbio.local"); await page.goto(publicationUrl);
    const second = page.getByRole("button", { name: "Aprobar como segundo FDE" });
    await expect(second).toBeVisible(); await second.click();
    await expect(second).not.toBeVisible();
    const client = page.getByRole("button", { name: "Marcar aprobación del cliente (confirmada fuera de la plataforma)" });
    if (await client.isVisible()) await client.click();
  }
  await expect(page.getByRole("status")).toHaveText("Activa al 100 %");
  await page.getByRole("link", { name: "Versiones", exact: true }).click();
  await expect(page.getByText("Activa al 100 %", { exact: true }).first()).toBeVisible();
});
