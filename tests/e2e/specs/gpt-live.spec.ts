import { test, expect, type WebSocketRoute } from "@playwright/test";

const chatBase = process.env.E2E_CHAT_BASE_URL ?? "http://localhost:15174";
const sessionId = "00000000-0000-4000-8000-000000000001";

test("GPT-Live keeps microphone live during delegated work and hides written chat", async ({ page }) => {
  let voice: WebSocketRoute;
  const capture: boolean[] = [];
  let connected = false;
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/chat/sessions") return route.fulfill({ json: { session_id: sessionId, tenant_name: "Demo", status: "active", websocket_token: "test-ticket", language: "es" } });
    if (url.pathname.endsWith("/voice/config")) return route.fulfill({ json: { provider: "OpenAI", transport: "gpt_live" } });
    if (url.pathname.endsWith("/voice")) {
      expect(route.request().postDataJSON()).toMatchObject({ consent: true, provider: "OpenAI" });
      return route.fulfill({ json: { signed_url: "/ws/voice/live/test?ticket=synthetic", session_token: "", transport: "gpt_live" } });
    }
    return route.abort();
  });
  await page.routeWebSocket("**/ws/chat/**", ws => ws.send(JSON.stringify({ type: "history", messages: [{
    id: "opening", direction: "out", channel: "web", client_message_id: "",
    text: "Soy el asistente virtual. ¿Qué experiencia tienes para este puesto?", created_at: "2026-10-27T14:00:00Z",
  }], status: "active" })));
  await page.routeWebSocket("**/ws/voice/live/**", ws => {
    voice = ws; connected = true;
    ws.onClose(() => { connected = false; });
    ws.onMessage(raw => {
      const data = JSON.parse(String(raw));
      expect(["audio", "stop", "ping"]).toContain(data.type);
      if (data.type === "audio") capture.push(Buffer.from(data.audio, "base64").some(byte => byte !== 0));
    });
    ws.send(JSON.stringify({ type: "ready", sample_rate: 16000 }));
  });
  await page.addInitScript(() => {
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination); oscillator.start(); await context.resume();
      for (const track of destination.stream.getTracks()) {
        const original = track.stop.bind(track);
        track.stop = () => { original(); oscillator.stop(); void context.close(); };
      }
      return destination.stream;
    };
  });
  await page.goto(`${chatBase}/c/live-protocol-test`);
  await page.getByLabel("Tu mensaje").fill("Borrador privado");
  await page.getByRole("button", { name: "Hablar aquí", exact: true }).click();
  await expect(page.getByRole("checkbox")).toHaveAccessibleName("Quiero activar el micrófono y continuar por voz.");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Empezar a hablar", exact: true }).click();
  await expect.poll(() => connected).toBe(true);
  await expect.poll(() => capture.some(Boolean)).toBe(true);
  voice!.send(JSON.stringify({ type: "backend", busy: true }));
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect(page.getByText("Estoy revisando lo que me has contado. Puedes seguir hablando.")).toBeVisible();
  const before = capture.length;
  await expect.poll(() => capture.length).toBeGreaterThan(before + 2);
  expect(capture.slice(-3)).toEqual([true, true, true]);
  // GPT-Live emits continuous silent frames between utterances. They must not
  // leave the visual state permanently at "speaking" or pause capture.
  for (let i = 0; i < 10; i++) voice!.send(JSON.stringify({ type: "audio", delta: Buffer.alloc(3200).toString("base64") }));
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  // A conversational acknowledgment plays while the backend is still busy.
  const acknowledgment = Buffer.alloc(3200);
  for (let i = 0; i < acknowledgment.length; i += 2) acknowledgment.writeInt16LE(1000, i);
  voice!.send(JSON.stringify({ type: "audio", delta: acknowledgment.toString("base64") }));
  await expect(page.getByRole("heading", { name: "El asistente está hablando" })).toBeVisible();
  await expect.poll(() => capture.length).toBeGreaterThan(before + 4);
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect(page.getByLabel("Tu mensaje")).toBeHidden();
  await expect(page.getByLabel("Tu mensaje")).toBeDisabled();
  voice!.send(JSON.stringify({ type: "user_speaking" }));
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Volver al chat" }).click();
  await expect(page.getByLabel("Tu mensaje")).toHaveValue("Borrador privado");
  await expect.poll(() => connected).toBe(false);
});
