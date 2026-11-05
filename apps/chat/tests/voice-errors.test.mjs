import assert from "node:assert/strict";
import test from "node:test";
import { dictionaries } from "../src/i18n.ts";
import { voiceCloseMessage as localizedClose, voiceStartMessage as localizedStart } from "../src/components/voiceErrors.ts";

const { es, en } = dictionaries;
const voiceStartMessage = (status, error, messages = es) => localizedStart(status, error)(messages);
const voiceCloseMessage = (code, reason, messages = es) => localizedClose(code, reason)?.(messages) ?? "";

test("voice startup preserves actionable validation errors", () => {
  assert.equal(voiceStartMessage(409, { detail: "Revisa el consentimiento." }), "Revisa el consentimiento.");
});

test("voice startup does not expose provider details or malformed errors", () => {
  for (const error of [null, "secret", { detail: ["secret"] }, { message: "secret" }]) {
    assert.doesNotMatch(voiceStartMessage(429, error), /secret/);
  }
  assert.doesNotMatch(voiceStartMessage(500, { detail: "secret" }), /secret/);
});

test("exhausted capacity stays unavailable without exposing quotas in either language", () => {
  for (const messages of [es, en]) {
    const text = voiceCloseMessage(4300, "Agent private-agent has exceeded its daily call limit of 5.", messages);
    assert.equal(text, messages.voiceErrors.unavailableNow);
    assert.doesNotMatch(text, /private-agent|limit|límite|quota|cuota|5/i);
    assert.equal(voiceStartMessage(429, { detail: "Límite local de llamadas alcanzado." }, messages), messages.voiceErrors.unavailableNow);
  }
});

test("4300 is not assumed to mean daily limit without a reason", () => {
  assert.doesNotMatch(voiceCloseMessage(4300, ""), /límite diario/);
});

test("concurrency and credit limits are distinct", () => {
  assert.match(voiceCloseMessage(4300, "concurrency limit reached"), /otra llamada/);
  assert.match(voiceCloseMessage(4300, "quota exceeded"), /no está disponible ahora/);
  assert.doesNotMatch(voiceCloseMessage(4300, "quota exceeded"), /proveedor|cuota|plan/);
});

test("candidate-facing errors never expose rehearsal labels or provider names", () => {
  for (const detail of ["Llamadas de prueba agotadas", "Simulación de voz", "Datos ficticios", "ElevenLabs no disponible", "Twilio 403", "OpenAI 429"]) {
    const text = voiceStartMessage(429, { detail });
    assert.doesNotMatch(text, /prueba|simulaci|fictici|ElevenLabs|Twilio|OpenAI/i);
    assert.match(text, /continuar por escrito/);
  }
});

test("callback failures are translated without exposing raw private details", () => {
  const text = voiceCloseMessage(1002, "custom_llm_error: https://private.test?token=secret");
  assert.match(text, /obtener una respuesta/);
  assert.doesNotMatch(text, /secret|private|https/);
});

test("normal closure is silent and unknown failures stay generic", () => {
  assert.equal(voiceCloseMessage(1000, ""), "");
  assert.doesNotMatch(voiceCloseMessage(1006, "private-data"), /private-data/);
});

test("English keeps the same safeguards and never shows Spanish server details", () => {
  assert.equal(voiceStartMessage(409, { detail: "Revisa el consentimiento." }, en), en.voiceErrors.startUnavailable);
  assert.equal(voiceStartMessage(429, { detail: "Twilio 403" }, en), en.voiceErrors.unavailableNow);
  assert.equal(voiceCloseMessage(4300, "daily call limit", en), en.voiceErrors.unavailableNow);
  assert.match(voiceCloseMessage(1002, "custom_llm_error: https://private.test", en), /couldn't get a reply/);
  assert.doesNotMatch(voiceCloseMessage(1006, "private-data", en), /private-data/);
  assert.equal(localizedClose(1000, ""), null);
});
