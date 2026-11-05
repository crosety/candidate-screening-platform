import { test, expect, type WebSocketRoute } from "@playwright/test";

const chatBase = process.env.E2E_CHAT_BASE_URL ?? "http://localhost:15174";
const greeting = {
  id: "opening", direction: "out", channel: "web", client_message_id: "",
  text: "Soy el asistente virtual de Grupo Sazón. ¿Qué experiencia tienes en cocina?",
  created_at: "2026-10-27T14:00:00Z",
};

test("assistant opens before text or voice; reloading preserves one greeting", async ({ page }) => {
  let socket: WebSocketRoute | undefined;
  let greeted = false;
  const submissions: string[] = [];
  await page.route("**/api/v1/chat/sessions", route => route.fulfill({ json: {
    session_id: "00000000-0000-4000-8000-000000000001", tenant_name: "Grupo Sazón",
    offer_title: "Cocinero/a · Ruzafa", status: "active", websocket_token: "test", language: "es",
  } }));
  await page.route("**/voice/config", route => route.fulfill({ json: {
    provider: "ElevenLabs", transport: "eleven_direct",
  } }));
  await page.routeWebSocket("**/ws/chat/**", ws => {
    socket = ws;
    ws.send(JSON.stringify({ type: "history", messages: greeted ? [greeting] : [], status: "active" }));
    ws.onMessage(message => submissions.push(String(message)));
  });
  await page.goto(`${chatBase}/c/opening-test`);
  await expect(page.getByRole("heading", { name: "El asistente está preparando tu entrevista" })).toBeVisible();
  await expect(page.getByLabel("Tu mensaje")).toBeDisabled();
  const voiceButton = page.getByRole("button", { name: "Hablar aquí", exact: true });
  const phoneButton = page.getByRole("button", { name: "Llámame", exact: true });
  await expect(voiceButton).toBeDisabled();
  await expect(phoneButton).toBeDisabled();
  expect(submissions).toEqual([]);
  greeted = true;
  socket!.send(JSON.stringify({ type: "message", message: greeting }));
  socket!.send(JSON.stringify({ type: "message", message: greeting }));
  await expect(page.getByText(greeting.text, { exact: true })).toHaveCount(1);
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  await expect(voiceButton).toBeEnabled();
  await expect(phoneButton).toBeEnabled();
  await page.reload();
  await expect(page.getByText(greeting.text, { exact: true })).toHaveCount(1);
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  expect(submissions).toEqual([]);
  await expect(page.getByText(/escrib.*persona|prefieres hablar con una persona/i)).toHaveCount(0);
  const voice = page.getByRole("dialog", { name: "Hablemos por aquí" });
  const draft = page.getByLabel("Tu mensaje");
  await draft.fill("Borrador sin enviar");
  await voiceButton.click();
  await expect(voice).toBeVisible();
  expect(await voice.evaluate(el => el.matches(":modal"))).toBe(true);
  const consent = voice.getByRole("checkbox", { name: "Quiero activar el micrófono y continuar por voz." });
  await expect(page.getByRole("button", { name: "Empezar a hablar" })).toBeDisabled();
  await consent.check();
  await expect(page.getByRole("button", { name: "Empezar a hablar" })).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(voice).not.toBeVisible();
  await expect(voiceButton).toBeFocused();
  await expect(draft).toHaveValue("Borrador sin enviar");
  await voiceButton.press("Enter");
  // Each opening requires fresh microphone consent, even if the draft survives.
  await expect(consent).not.toBeChecked();
  await expect(page.getByRole("button", { name: "Empezar a hablar" })).toBeDisabled();
  await page.setViewportSize({ width: 375, height: 812 });
  await expect(page.getByText(/escrib.*persona|prefieres hablar con una persona/i)).toHaveCount(0);
  const mobilePanel = await voice.boundingBox();
  expect(mobilePanel!.x).toBeGreaterThanOrEqual(0);
  expect(mobilePanel!.x + mobilePanel!.width).toBeLessThanOrEqual(375);
  expect(mobilePanel!.y).toBeGreaterThanOrEqual(0);
  expect(mobilePanel!.y + mobilePanel!.height).toBeLessThanOrEqual(812);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await voice.getByRole("button", { name: "Cerrar panel", exact: true }).click();
  await expect(voice).not.toBeVisible();
  await expect(draft).toHaveValue("Borrador sin enviar");
  expect(submissions).toEqual([]);
});
