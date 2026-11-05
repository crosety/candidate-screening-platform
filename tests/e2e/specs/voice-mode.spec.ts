import { test, expect, type Page, type WebSocketRoute } from "@playwright/test";
import { browserVoiceExit, startBrowserVoice } from "../support/voice-acceptance-ui.mjs";

// Browser regression with synthetic capture and a protocol double. No real
// candidate, database, ElevenLabs, credentials or model calls are involved.
const chatBase = process.env.E2E_CHAT_BASE_URL ?? "http://localhost:15174";
const sessionId = "00000000-0000-4000-8000-000000000001";
const initial = { id: "welcome", direction: "out", text: "Bienvenida de prueba", created_at: "2026-10-26T10:00:00Z" };

test("local quota rejection preserves the draft without activating the microphone", async ({ page }) => {
  const error = "Se ha alcanzado el límite local de 20 llamadas de prueba en 24 horas.";
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/chat/sessions") return route.fulfill({ json: { session_id: sessionId, tenant_name: "Prueba", status: "active", websocket_token: "test", language: "es" } });
    if (url.pathname.endsWith("/voice/config")) return route.fulfill({ json: { provider: "ElevenLabs", transport: "eleven_direct" } });
    if (url.pathname.endsWith("/voice")) return route.fulfill({ status: 429, json: { detail: error } });
    return route.abort();
  });
  await page.routeWebSocket("**/ws/chat/**", ws => ws.send(JSON.stringify({ type: "history", messages: [initial], status: "active" })));
  await page.addInitScript(() => {
    Object.assign(window, { microphoneRequests: 0 });
    navigator.mediaDevices.getUserMedia = async () => {
      (window as unknown as { microphoneRequests: number }).microphoneRequests++;
      throw new Error("Must not request the microphone after HTTP rejection");
    };
  });
  await page.goto(`${chatBase}/c/quota-test`);
  await page.getByLabel("Tu mensaje").fill("Borrador conservado");
  await page.getByRole("button", { name: "Hablar aquí", exact: true }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Empezar a hablar", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /^La conversación por voz/ })).toHaveText("La conversación por voz no está disponible ahora. Puedes continuar por escrito.");
  await expect(page.getByLabel("Tu mensaje")).toHaveValue("Borrador conservado");
  await expect(page.getByRole("log")).toBeVisible();
  expect(await page.evaluate(() => (window as unknown as { microphoneRequests: number }).microphoneRequests)).toBe(0);
});

function pcm(seconds: number): string {
  const bytes = Buffer.alloc(Math.round(seconds * 16000) * 2);
  for (let i = 0; i < bytes.length / 2; i++) bytes.writeInt16LE(Math.round(Math.sin(i * 0.05) * 1200), i * 2);
  return bytes.toString("base64");
}

async function setup(page: Page) {
  let chat: WebSocketRoute;
  let voice: WebSocketRoute;
  const microphone: boolean[] = [];
  let voiceClosed = false;
  const textSubmissions: string[] = [];
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/chat/sessions") return route.fulfill({ json: { session_id: sessionId, tenant_name: "Logística (prueba)", status: "active", websocket_token: "test-ticket", language: "es" } });
    if (url.pathname.endsWith("/voice/config")) return route.fulfill({ json: { provider: "ElevenLabs", transport: "speech_engine" } });
    if (url.pathname === `/api/v1/chat/sessions/${sessionId}/voice`) return route.fulfill({ json: { signed_url: `${chatBase.replace("http", "ws")}/voice-protocol-double`, session_token: "test-signature" } });
    return route.abort();
  });
  await page.routeWebSocket("**/ws/chat/**", ws => {
    chat = ws;
    ws.send(JSON.stringify({ type: "history", messages: [initial], status: "active" }));
    ws.onMessage(message => textSubmissions.push(String(message)));
  });
  await page.routeWebSocket("**/voice-protocol-double", ws => {
    voice = ws;
    ws.onClose(() => { voiceClosed = true; });
    ws.onMessage(raw => {
      const message = JSON.parse(String(raw));
      if (message.type === "conversation_initiation_client_data") {
        expect(message.dynamic_variables).toEqual({ orbio_session_token: "test-signature" });
        expect(message.custom_llm_extra_body).toBeUndefined();
        ws.send(JSON.stringify({ type: "conversation_initiation_metadata", conversation_initiation_metadata_event: { user_input_audio_format: "pcm_16000", agent_output_audio_format: "pcm_16000" } }));
      }
      if (message.user_audio_chunk) microphone.push(Buffer.from(message.user_audio_chunk, "base64").some(byte => byte !== 0));
    });
  });
  await page.addInitScript(() => {
    // Feed a continuous oscillator: it represents speech/echo even while the
    // assistant is thinking, precisely the case a one-utterance fixture misses.
    navigator.mediaDevices.getUserMedia = async () => {
      const context = new AudioContext();
      const source = context.createOscillator();
      const destination = context.createMediaStreamDestination();
      source.connect(destination); source.start(); await context.resume();
      for (const track of destination.stream.getTracks()) {
        const original = track.stop.bind(track);
        // MediaStreamTrack.stop is idempotent, including repeated browser cleanup.
        let stopped = false;
        track.stop = () => { if (stopped) return; stopped = true; original(); source.stop(); void context.close(); };
      }
      return destination.stream;
    };
    const probe = { ended: 0, stopped: 0 };
    Object.assign(window, { voicePlaybackProbe: probe });
    const start = AudioBufferSourceNode.prototype.start;
    const stop = AudioBufferSourceNode.prototype.stop;
    AudioBufferSourceNode.prototype.start = function (...args) {
      this.addEventListener("ended", () => { probe.ended++; });
      return start.apply(this, args);
    };
    AudioBufferSourceNode.prototype.stop = function (...args) { probe.stopped++; return stop.apply(this, args); };
  });
  await page.goto(`${chatBase}/c/protocol-double`);
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  await page.getByLabel("Tu mensaje").fill("Borrador conservado");
  await page.getByRole("button", { name: "Hablar aquí", exact: true }).click();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Empezar a hablar", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect.poll(() => microphone.some(Boolean)).toBe(true);
  return {
    sendVoice: (event: object) => voice.send(JSON.stringify(event)),
    sendChat: (event: object) => chat.send(JSON.stringify(event)),
    closeVoice: () => voice.close({ code: 1000, reason: "Finished" }),
    microphone, textSubmissions,
    isVoiceClosed: () => voiceClosed,
  };
}

test("voice is exclusive; text is hidden, disabled, preserved and restored", async ({ page }) => {
  const state = await setup(page);
  await expect(page.getByRole("log")).toBeHidden();
  await expect(page.getByLabel("Tu mensaje")).toBeHidden();
  await expect(page.getByLabel("Tu mensaje")).toBeDisabled();
  await page.screenshot({ path: test.info().outputPath("voice-desktop.png"), fullPage: true });
  state.sendChat({ type: "message", message: { ...initial, id: "reply", text: "Respuesta guardada, sin mostrar durante voz" } });
  await expect(page.getByText("Respuesta guardada, sin mostrar durante voz")).toBeHidden();
  expect(state.textSubmissions).toEqual([]);
  await page.getByRole("button", { name: "Volver al chat" }).click();
  await expect(page.getByLabel("Tu mensaje")).toHaveValue("Borrador conservado");
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  await expect(page.getByText("Respuesta guardada, sin mostrar durante voz")).toBeVisible();
  await expect.poll(state.isVoiceClosed).toBe(true);
});

test("live acceptance controls reopen the current voice sheet and preserve the text draft", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await setup(page);
  await browserVoiceExit(page).click();
  await expect(page.getByLabel("Tu mensaje")).toHaveValue("Borrador conservado");
  await startBrowserVoice(page);
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect(page.getByRole("log")).toBeHidden();
  await browserVoiceExit(page).click();
  await expect(page.getByLabel("Tu mensaje")).toHaveValue("Borrador conservado");
  expect(errors).toEqual([]);
});

test("speech during the filler cannot cancel the final answer or unmute early", async ({ page }) => {
  const state = await setup(page);
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "Hola" } });
  state.sendVoice({ type: "audio", audio_event: { audio_base_64: pcm(0.2), event_id: 1 } });
  state.sendVoice({ type: "agent_response", agent_response_event: { agent_response: "Estoy revisando tu respuesta..." } });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  await expect.poll(() => state.microphone.slice(-3)).toEqual([false, false, false]);
  state.sendVoice({ type: "agent_response", agent_response_event: { agent_response: "Estoy revisando tu respuesta... ¿Qué turnos puedes hacer?" } });
  state.sendVoice({ type: "audio", audio_event: { audio_base_64: pcm(1.2), event_id: 1 } });
  await expect(page.getByRole("heading", { name: "El asistente está hablando" })).toBeVisible();
  await expect(page.getByText(/¿Qué turnos puedes hacer/)).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Te escucho" })).toBeVisible();
  await expect.poll(() => state.microphone.slice(-3)).toEqual([true, true, true]);
  await page.getByRole("button", { name: "Volver al chat" }).click();
});

test("a completed session and normal provider close drain the final spoken answer", async ({ page }) => {
  const state = await setup(page);
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "Sí" } });
  state.sendChat({ type: "status", status: "completed" });
  state.sendVoice({ type: "agent_response", agent_response_event: { agent_response: "Gracias. El equipo revisará tu candidatura." } });
  state.sendVoice({ type: "audio", audio_event: { audio_base_64: pcm(1.5), event_id: 1 } });
  await expect(page.getByRole("heading", { name: "El asistente está hablando" })).toBeVisible();
  state.closeVoice();
  await expect(page.getByRole("button", { name: "Volver al chat" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Volver al chat" })).toHaveCount(0);
  const audio = await page.evaluate(() => (window as unknown as { voicePlaybackProbe: { ended: number; stopped: number } }).voicePlaybackProbe);
  expect(audio).toEqual({ ended: 1, stopped: 0 });
  await expect(page.getByRole("log")).toBeVisible();
  await expect(page.getByLabel("Tu mensaje")).toHaveCount(0);
});

test("manual exit while waiting stays available after the session ends", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const state = await setup(page);
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "Sí" } });
  state.sendChat({ type: "status", status: "completed" });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: test.info().outputPath("voice-mobile.png"), fullPage: true });
  const exit = page.getByRole("button", { name: "Volver al chat" });
  await expect(exit).toBeEnabled();
  await exit.click();
  await expect(page.getByRole("log")).toBeVisible();
});

test("a stalled answer releases the microphone and restores text with an error", async ({ page }) => {
  const state = await setup(page);
  await page.clock.install();
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "Hola" } });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  await page.clock.fastForward(90001);
  await expect(page.getByRole("alert")).toContainText("tardando demasiado");
  await expect(page.getByRole("log")).toBeVisible();
  await expect(page.getByLabel("Tu mensaje")).toBeEnabled();
  await expect.poll(state.isVoiceClosed).toBe(true);
});

test("a provider silence marker does not pause listening or create a false turn", async ({ page }) => {
  const state = await setup(page);
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "..." } });
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect.poll(() => state.microphone.slice(-3)).toEqual([true, true, true]);
  await page.getByRole("button", { name: "Volver al chat" }).click();
});

test("upstream processing pauses capture before the provider sends its delayed transcript", async ({ page }) => {
  const state = await setup(page);
  // Production Speech Engine sent this browser event only alongside TTS, after
  // 28 seconds. The authoritative chat signal must work without that event.
  state.sendChat({ type: "typing", on: true });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  await expect.poll(() => state.microphone.slice(-3)).toEqual([false, false, false]);
  state.sendChat({ type: "typing", on: false });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  state.sendVoice({ type: "user_transcript", user_transcription_event: { user_transcript: "Hola" } });
  state.sendVoice({ type: "agent_response", agent_response_event: { agent_response: "¿Cuál es tu certificado?" } });
  state.sendVoice({ type: "audio", audio_event: { audio_base_64: pcm(1), event_id: 1 } });
  await expect(page.getByRole("heading", { name: "El asistente está hablando" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Te escucho", exact: true })).toBeVisible();
  await expect.poll(() => state.microphone.slice(-3)).toEqual([true, true, true]);
  state.sendChat({ type: "typing", on: true });
  await expect(page.getByRole("heading", { name: "Preparando respuesta" })).toBeVisible();
  await page.getByRole("button", { name: "Volver al chat" }).click();
  state.sendChat({ type: "typing", on: false });
  await expect(page.getByRole("log")).toBeVisible();
});
