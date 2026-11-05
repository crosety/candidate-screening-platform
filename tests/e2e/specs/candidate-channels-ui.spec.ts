import { expect, test, type Locator } from "@playwright/test";
import type { components } from "../../../packages/api-client/src/schema";
import { createChat, login } from "./helpers";

test.use({ timezoneId: "America/New_York" });

test("un rechazo del formato de móvil permite corregirlo sin repetir la llamada", async ({ page, context }) => {
  await login(page);
  await context.route("**/chat/telephone/status", route => route.fulfill({ json: { available: true, sender: "", attempt: null, reason: "" } }));
  let requests = 0;
  await context.route("**/chat/telephone/request", route => {
    requests++;
    return route.fulfill({ status: 400, json: { detail: "Indica un móvil español válido con prefijo +34." } });
  });
  const chat = await createChat(page, "Validación del móvil");
  await chat.getByRole("button", { name: "English", exact: true }).click();
  await chat.getByRole("button", { name: "Call me", exact: true }).click();
  await chat.getByRole("textbox", { name: "Your mobile number", exact: true }).fill("+34 600 000 001");
  await chat.getByRole("checkbox").check();
  await chat.getByRole("button", { name: "Call me now", exact: true }).click();
  await expect(chat.getByRole("alert")).toHaveText("Enter a valid Spanish mobile number with the +34 prefix.");
  expect(requests).toBe(1);
  for (const width of [375, 1366]) {
    await chat.setViewportSize({ width, height: 900 });
    expect(await chat.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await chat.screenshot({ path: test.info().outputPath(`telephone-number-${width}.png`) });
  }
});

async function readable(locator: Locator) {
  await expect(locator).toBeVisible();
  const contrast = await locator.evaluate(element => {
    const rgb = (text: string) => text.match(/[\d.]+/g)!.slice(0, 3).map(Number);
    const luminance = (color: number[]) => color.map(value => {
      const s = value / 255;
      return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    }).reduce((total, value, index) => total + value * [0.2126, 0.7152, 0.0722][index], 0);
    let background = element;
    while (background.parentElement && getComputedStyle(background).backgroundColor === "rgba(0, 0, 0, 0)") background = background.parentElement;
    const foreground = luminance(rgb(getComputedStyle(element).color));
    const surface = luminance(rgb(getComputedStyle(background).backgroundColor));
    return (Math.max(foreground, surface) + 0.05) / (Math.min(foreground, surface) + 0.05);
  });
  expect(contrast).toBeGreaterThanOrEqual(4.5);
}

test("teléfono y SMS: contratos, consentimiento y textos legibles en móvil y escritorio", async ({ page, context }) => {
  await login(page);
  const status: components["schemas"]["TelephoneStatusOut"] = { available: true, sender: "", attempt: null, reason: "" };
  const sms: components["schemas"]["SmsStatusOut"] = { available: true, notifications: [] };
  let calls = 0;
  await context.route("**/chat/telephone/status", route => route.fulfill({ json: status }));
  await context.route("**/chat/telephone/request", route => {
    expect(route.request().postDataJSON()).toMatchObject({ phone: "+34600000000", consent: true });
    calls++;
    status.attempt = { id: "ui-call", state: "queued", reason: "" };
    return route.fulfill({ json: status.attempt });
  });
  await context.route("**/chat/sms-link", route => {
    const body = route.request().postDataJSON();
    if (body.action === "send") {
      expect(body).toMatchObject({ phone: "+34600000000", consent: true });
      sms.notifications = [{ id: "ui-sms", purpose: "invitation", state: "queued", revoked: false, created_at: "2026-10-29T10:00:00Z", segments: 1 }];
      return route.fulfill({ json: sms.notifications[0] });
    }
    if (body.action === "revoke") { sms.notifications[0].revoked = true; return route.fulfill({ json: { revoked: true } }); }
    return route.fulfill({ json: sms });
  });
  const chat = await createChat(page, "Canales de interfaz");
  for (const width of [375, 1366]) {
    await chat.setViewportSize({ width, height: 900 });
    for (const action of await chat.locator(".candidate-meta .chat-meta-action").all()) await readable(action);
    await chat.getByRole("button", { name: "Llámame", exact: true }).click();
    await expect(chat.getByRole("button", { name: "Llámame ahora", exact: true })).toBeDisabled();
    await readable(chat.locator(".sheet-footnote"));
    expect(await chat.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await chat.screenshot({ path: test.info().outputPath(`telephone-${width}.png`) });
    await chat.getByRole("dialog").getByRole("button", { name: "Cerrar panel", exact: true }).click();
  }
  await chat.getByRole("button", { name: "Llámame", exact: true }).click();
  await chat.getByRole("textbox", { name: "Tu móvil" }).fill("+34 600 000 000");
  await chat.getByRole("checkbox").check();
  await chat.getByRole("button", { name: "Llámame ahora", exact: true }).click();
  await expect(chat.getByRole("button", { name: "Ver llamada", exact: true })).toBeVisible();
  expect(calls).toBe(1);
  await chat.getByRole("button", { name: "Mi enlace por SMS", exact: true }).click();
  await chat.getByRole("textbox", { name: "Tu móvil" }).fill("+34 600 000 000");
  await chat.getByRole("checkbox").check();
  await chat.getByRole("button", { name: "Enviarme el enlace", exact: true }).click();
  await expect(chat.getByRole("status").filter({ hasText: "Preparando tu enlace" })).toBeVisible();
  await chat.getByRole("button", { name: "No recibir más avisos", exact: true }).click();
  await expect(chat.getByText(/Avisos desactivados/)).toBeVisible();
});

test("un fallo de SMS se puede reintentar en ambos idiomas sin fingir disponibilidad", async ({ page, context }) => {
  await login(page);
  let failed = true;
  await context.route("**/chat/telephone/status", route => route.fulfill({ json: { available: false, sender: "", attempt: null, reason: "Se ha agotado el cupo autorizado de llamadas" } }));
  await context.route("**/chat/sms-link", route => failed
    ? route.fulfill({ status: 503, json: { detail: "private-provider" } })
    : route.fulfill({ json: { available: false, notifications: [] } }));
  const chat = await createChat(page, "SMS no disponible");
  await chat.setViewportSize({ width: 375, height: 900 });
  await expect(chat.getByRole("alert").filter({ hasText: "No hemos podido comprobar los SMS." })).toHaveCount(0);
  await chat.getByRole("button", { name: "Mi enlace por SMS", exact: true }).click();
  await expect(chat.getByRole("alert").filter({ hasText: "No hemos podido comprobar los SMS." })).toBeVisible();
  await chat.screenshot({ path: test.info().outputPath("sms-error-375.png") });
  await chat.getByRole("button", { name: "Cerrar panel", exact: true }).click();
  await chat.getByRole("button", { name: "Llámame", exact: true }).click();
  await expect(chat.getByText("Las llamadas no están disponibles ahora. Puedes continuar por escrito.", { exact: true })).toBeVisible();
  await expect(chat.getByRole("button", { name: "Llámame ahora", exact: true })).toHaveCount(0);
  await chat.getByRole("button", { name: "Cerrar panel", exact: true }).click();
  await chat.getByRole("button", { name: "English", exact: true }).click();
  await chat.getByRole("button", { name: "Text me my link", exact: true }).click();
  await expect(chat.getByRole("alert").filter({ hasText: "We couldn't check your SMS messages." })).toBeVisible();
  await chat.getByRole("button", { name: "Close", exact: true }).click();
  await chat.getByRole("button", { name: "Call me", exact: true }).click();
  await expect(chat.getByText("Calls aren't available right now. You can keep going in writing.", { exact: true })).toBeVisible();
  await chat.getByRole("button", { name: "Close", exact: true }).click();
  await chat.getByRole("button", { name: "Text me my link", exact: true }).click();
  failed = false;
  await chat.getByRole("button", { name: "Retry SMS", exact: true }).click();
  await expect(chat.getByText("SMS isn't available right now. You can keep going in this chat.")).toBeVisible();
  await chat.screenshot({ path: test.info().outputPath("sms-unavailable-375.png") });
  await expect(chat.getByRole("button", { name: "Send me the link", exact: true })).toHaveCount(0);
  await expect(chat.getByText("private-provider")).toHaveCount(0);
});

test("selección ve el error de SMS inicial y recupera el estado", async ({ page, context }) => {
  await login(page);
  let sid = "";
  await context.route("**/chat/telephone/status", route => {
    sid = route.request().postDataJSON().session_id;
    return route.fulfill({ json: { available: false, reason: "", sender: "", attempt: null } });
  });
  const chat = await createChat(page, "SMS de selección");
  await expect.poll(() => sid).not.toBe("");
  await chat.close();
  let failed = true;
  await page.route("**/sessions/*/sms-link", route => failed
    ? route.fulfill({ status: 503, json: { detail: "Unavailable" } })
    : route.fulfill({ json: { available: false, notifications: [] } }));
  await page.goto(`${new URL(page.url()).pathname.replace(/\/[^/]+$/, "")}/candidaturas/${sid}`);
  await expect(page.getByRole("alert").filter({ hasText: "No se pudo consultar el estado del SMS." })).toBeVisible();
  failed = false;
  await page.getByRole("button", { name: "Reintentar SMS", exact: true }).click();
  await page.getByText("Invitación por SMS", { exact: true }).click();
  await expect(page.getByText("El envío de SMS no está disponible ahora.")).toBeVisible();
});

test("la agenda identifica la zona de la hora incluso para quien consulta desde otro país", async ({ page }) => {
  await login(page);
  const interview: components["schemas"]["InterviewOut"] = {
    id: "00000000-0000-4000-8000-000000000001", session_id: "00000000-0000-4000-8000-000000000002", candidate_name: "Agenda sintética", site_ref: "local", site_name: "Local en Madrid", site_address: "", site_timezone: "Europe/Madrid",
    starts_at: "2026-11-02T10:00:00Z", starts_at_local: "2026-11-02T11:00:00+01:00", candidate_timezone: "Europe/Madrid", candidate_time: "11:00", duration_minutes: 15, status: "booked", sequence: 0, cancelled_by: "", reminder_at: null, reminder_status: "pending",
  };
  await page.route("**/interviews?**", route => route.fulfill({ json: [interview] }));
  await page.getByRole("navigation").getByRole("link", { name: "Agenda", exact: true }).click();
  await expect(page.getByText("11:00 CET", { exact: true })).toBeVisible();
  for (const width of [375, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    await page.screenshot({ path: test.info().outputPath(`agenda-${width}.png`), fullPage: true });
  }
  await page.route("**/interviews?**", route => route.fulfill({ status: 503, json: { detail: "No se ha podido cargar la agenda." } }));
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: "No se ha podido cargar la agenda." })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("agenda-error-1366.png"), fullPage: true });
});

test("los denominadores y definiciones de analítica son legibles sin hover", async ({ page }) => {
  await login(page);
  const chat = await createChat(page, "Contraste de analítica");
  await chat.close();
  await page.getByRole("navigation").getByRole("link", { name: "Analítica", exact: true }).click();
  await page.getByLabel("Datos", { exact: true }).selectOption("simulation");
  await expect(page.locator(".analytics-times")).toBeVisible();
  await expect(page.locator(".analytics-table td small").first()).toBeVisible();
  for (const width of [375, 1366]) {
    await page.setViewportSize({ width, height: 900 });
    for (const text of await page.locator(".analytics-note, .analytics-times .surface > p:last-child, .chart-heading p, .analytics-table td small, .stack-label small").all()) await readable(text);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: test.info().outputPath(`analytics-${width}.png`), fullPage: true });
  }
  await page.route("**/analytics?**", route => route.fulfill({ status: 503, json: { detail: "Unavailable" } }));
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: "No se puede cargar la analítica" })).toBeVisible();
  await page.screenshot({ path: test.info().outputPath("analytics-error-1366.png"), fullPage: true });
});
