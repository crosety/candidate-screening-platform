// Labels for conversation observations. They describe tone and language only;
// they are never selection criteria.
export const moods = ["neutral", "positive", "confused", "anxious", "frustrated", "unknown"] as const;
const moodLabels: Record<string, string> = {
  neutral: "Neutral", positive: "Positivo", confused: "Confusión", anxious: "Inquietud",
  frustrated: "Frustración", unknown: "Sin observación",
};
export function moodLabel(value: string) { return moodLabels[value] ?? value; }

const channelLabels: Record<string, string> = { web: "Web", whatsapp: "WhatsApp", voice: "Voz", telephone: "Teléfono", sim: "Simulación", sms: "SMS" };
export function channelLabel(value: string) { return channelLabels[value] ?? value; }

let names: Intl.DisplayNames | null = null;
export function languageLabel(code: string) {
  if (!code || code === "unknown") return "Sin observar";
  try {
    names ??= new Intl.DisplayNames(["es"], { type: "language" });
    const name = names.of(code);
    return name && name !== code ? name.charAt(0).toUpperCase() + name.slice(1) : code.toUpperCase();
  } catch {
    return code.toUpperCase();
  }
}
