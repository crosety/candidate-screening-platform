import { test, expect } from "@playwright/test";
import { login, selectTenant, createChat } from "./helpers";
test("el chat persiste y se consulta en la consola", async ({ page }) => {
  await login(page); await selectTenant(page, "Grupo Sazón");
  const name = `Candidata e2e ${Date.now()}`;
  const chat = await createChat(page, name);
  await chat.setViewportSize({ width: 390, height: 844 });
  await expect(chat.getByRole("log", { name: "Conversación", exact: true })).toBeVisible();
  await expect(chat.getByRole("button", { name: "Enviar mensaje", exact: true })).toBeDisabled();
  expect(await chat.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(chat.getByText(/asistente virtual/).last()).toBeVisible();
  await chat.getByLabel("Tu mensaje").fill("Hola");
  await chat.getByRole("button", { name: "Enviar" }).click();
  await expect(chat.getByText("He recibido: Hola", { exact: false })).toBeVisible();
  await page.getByRole("link", { name: "Trazas técnicas", exact: true }).click();
  await page.getByRole("link", { name: `Abrir conversación de ${name}`, exact: true }).click();
  await expect(page.getByText("He recibido: Hola", { exact: false })).toBeVisible();
  await expect(page.getByText("Hola", { exact: true })).toBeVisible();
});
