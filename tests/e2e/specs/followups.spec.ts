import { test, expect } from "@playwright/test";
import { login, selectTenant, createChat } from "./helpers";

test("el FDE aprueba y cancela la propuesta sin saltarse el dial", async ({ page }) => {
  // Logística has followup=1 (proposal); Grupo Sazón has followup=2 (audited
  // execution). Keep the approval test on the profile that requires approval.
  await login(page); await selectTenant(page, "Logística");
  const name = `Reenganche e2e ${Date.now()}`;
  const chat = await createChat(page, name, { legacyRef: "ALMACEN-E2E" });
  await expect(chat.getByText(/asistente virtual/).last()).toBeVisible();
  await page.getByRole("navigation", { name: "Navegación del cliente" }).getByRole("link", { name: "Simulación", exact: true }).click();
  await page.getByRole("button", { name: "Mensajería", exact: true }).click();
  const selector = page.getByLabel("Candidatura del teléfono");
  const option = await selector.locator("option").filter({ hasText: name }).getAttribute("value");
  if (!option) throw new Error("No aparece la candidatura creada");
  await selector.selectOption(option);
  await page.getByRole("button", { name: "Proponer recordatorio en 2 h" }).click();
  await expect(page.getByRole("status")).toContainText("requiere aprobación");
  await page.getByRole("link", { name: "Recordatorios", exact: true }).click();
  const row = page.getByRole("row").filter({ hasText: name });
  await expect(row).toContainText("Pendiente de aprobación");
  await row.getByRole("button", { name: "Aprobar", exact: true }).click();
  await expect(row).toContainText("Programado");
  await row.getByRole("button", { name: "Cancelar", exact: true }).click();
  await expect(row).toContainText("Cancelado");
});
