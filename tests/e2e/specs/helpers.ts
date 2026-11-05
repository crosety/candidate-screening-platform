import { expect, test, type Page } from "@playwright/test";
export async function login(page: Page, email = "fde@orbio.local") {
  const password = process.env.SEED_PASSWORD;
  if (!password) throw new Error("Falta SEED_PASSWORD");
  await page.goto("/login");
  await page.getByLabel("Email", { exact: true }).fill(email);
  await page.getByLabel("Contraseña").fill(password);
  const submit = async () => {
    const response = page.waitForResponse((r) => r.url().endsWith("/api/v1/auth/login") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Entrar", exact: true }).click();
    return response;
  };
  let response = await submit();
  if (response.status() === 429) {
    // Serial UI tests share an IP/account. Respect the real one-minute window;
    // do not disable the public rate limiter in the isolated E2E stack.
    test.setTimeout(test.info().timeout + 65_000);
    await page.waitForTimeout(60_100);
    response = await submit();
  }
  expect(response.ok()).toBe(true);
  await expect(page.getByRole("navigation")).toBeVisible();
}
export async function selectTenant(page: Page, name: string) {
  await page.getByRole("combobox", { name: "Cliente", exact: true }).click();
  await page.getByRole("option", { name, exact: true }).click();
}
export async function createChat(page: Page, name: string, offer: { key: string } | { legacyRef: string } = { key: "repartidores" }) {
  await page.getByRole("navigation", { name: "Navegación del cliente" }).getByRole("link", { name: "Simulación", exact: true }).click();
  await page.getByRole("button", { name: "Crear prueba · texto y voz", exact: true }).click();
  await page.getByLabel("Nombre", { exact: true }).fill(name);
  await page.getByLabel("Ciudad", { exact: true }).fill("València");
  if ("key" in offer) await page.getByRole("combobox", { name: "Oferta publicada", exact: true }).selectOption(offer.key);
  else await page.getByRole("textbox", { name: "Puesto", exact: true }).fill(offer.legacyRef);
  await page.getByRole("button", { name: "Crear candidatura", exact: true }).click();
  const link = page.getByRole("status").getByRole("link");
  await expect(link).toBeVisible();
  const url = await link.getAttribute("href");
  if (!url) throw new Error("No hay enlace");
  const chat = await page.context().newPage();
  // The assertions use the Spanish copy; pin it instead of following the browser locale.
  const chatUrl = new URL(url, page.url());
  chatUrl.searchParams.set("lang", "es");
  await chat.goto(chatUrl.href);
  await expect(chat.getByLabel("Tu mensaje")).toBeEnabled();
  return chat;
}
