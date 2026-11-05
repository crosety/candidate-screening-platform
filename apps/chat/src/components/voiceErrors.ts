import { serverText, type Localized } from "../i18n.ts";

const hiddenDetail = /prueba|simulaci|fictici|ElevenLabs|OpenAI|Twilio|l[ií]mite|limit|quota|cuota|cupo|presupuesto|budget|credits?|créditos/i;

/** Translate known provider failures without exposing raw reasons, identifiers or URLs. */
export function voiceStartMessage(status: number, error: unknown): Localized {
  // Only show application validation messages, never arbitrary upstream errors.
  if ([400, 403, 404, 409, 422, 429].includes(status) && error && typeof error === "object" && "detail" in error && typeof error.detail === "string") {
    if (hiddenDetail.test(error.detail)) return m => m.voiceErrors.unavailableNow;
    return serverText(error.detail, m => m.voiceErrors.startUnavailable);
  }
  return m => m.voiceErrors.startUnavailable;
}

/** `null` for a normal closure, which needs no notice. */
export function voiceCloseMessage(code: number, reason: string): Localized | null {
  const value = reason.toLowerCase();
  if (/daily (call )?limit/.test(value)) return m => m.voiceErrors.dailyLimit;
  if (/concurren/.test(value)) return m => m.voiceErrors.concurrency;
  if (/quota|credits?|creditos|payment/.test(value)) return m => m.voiceErrors.unavailableNow;
  if (/custom.?llm/.test(value)) return m => m.voiceErrors.callback;
  if (code === 1000) return null;
  return m => m.voiceErrors.closed;
}
