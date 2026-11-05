import type { components } from "@screening/api-client";

export type ProfileContent = components["schemas"]["ProfileContent"];
export type CapabilityId = NonNullable<ProfileContent["grants"]["conversador"]>[number];
export const briefSections = [
  ["role", "El puesto y el cliente", "Qué hace la empresa, qué puesto es y qué pasa después del cribado."],
  ["who", "A quién buscamos", "Lo imprescindible, lo que se valora y lo que el agente tiene que averiguar."],
  ["fit", "Encaje con el trabajo", "Cómo decidir si una persona encaja con una oportunidad, con las palabras del cliente."],
  ["voice", "Cómo hablar", "Persona, tono, longitud de los mensajes e idiomas."],
  ["followup", "Seguimiento", "Cuándo y cómo volver a escribir a quien se calla."],
  ["avoid", "Qué no hacer", "Límites propios del cliente. Los de la plataforma se aplican siempre."],
] as const;
export const capabilities: Record<"conversador" | "recruiter", CapabilityId[]> = {
  conversador: ["openings.find", "application.read_mine", "interview.book", "handoff.request", "credential.verify", "followup.schedule", "faq.search"],
  recruiter: ["candidates.query", "analytics.aggregates"],
};
export const autonomyLabels = {
  answer: "Responder dudas", schedule: "Agendar entrevistas", followup: "Reenganchar",
  handoff: "Derivar a una persona", qualify: "Marcar como apto", reject: "Descartar", brief: "Cambiar su propio perfil",
} as const;
export const autonomyLevels = ["Decide una persona", "Propone", "Decide y se audita", "Decide solo"];
export const riskLabels: Record<string, string> = { none: "Sin cambios", low: "Bajo", medium: "Medio", high: "Alto" };
