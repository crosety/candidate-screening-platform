import { serverText, type Localized } from "../i18n.ts";

export function normalizeMobile(value: string): string {
  return value.replace(/[\s()-]/g, "");
}

export function validMobile(value: string): boolean {
  return /^\+34(?:6\d{8}|7[1-4]\d{7})$/.test(normalizeMobile(value));
}

export function channelMessage(detail: unknown, fallback: Localized): Localized {
  if (detail === "Indica un móvil español válido con prefijo +34.") return messages => messages.telephone.invalidMobile;
  if (detail === "Las llamadas están disponibles de 08:00 a 22:00 en tu horario local.") return messages => messages.telephone.callHours;
  if (typeof detail !== "string" || /\b(?:demo|pruebas?|simulad[oa]s?|simulaci[oó]n|fictici[oa]s?|Twilio|ElevenLabs|OpenAI|túnel|ventana|l[ií]mites?|limits?|quota|cuota|cupo|presupuesto|budget|credits?|créditos)\b/i.test(detail)) return fallback;
  return serverText(detail, fallback);
}

export function channelFailure(error: unknown, fallback: Localized): Localized {
  return channelMessage(error && typeof error === "object" && "detail" in error ? error.detail : undefined, fallback);
}
