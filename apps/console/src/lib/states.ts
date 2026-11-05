const labels: Record<string, string> = {
  active: "Activa", completed: "Completada", handed_off: "Derivada", abandoned: "Cerrada",
  qualified: "Apto", not_qualified: "No apto", review: "Revisar", pool: "Bolsa",
  qualify: "Aprobar", reject: "Descartar", applied: "Aplicada", overridden: "Corregida",
  proposed: "Propuesta", evaluating: "Evaluando", ready: "Lista para revisar", rejected: "Rechazada", accepted: "Aceptada",
  reserved: "Reservado", running: "En curso", cancelled: "Cancelado", failed: "Fallido", done: "Terminado", over_budget: "Exceso estimado",
  opening: "Inicio", requirements: "Requisitos", matching: "Encaje", closing: "Cierre",
  positive: "Positivo", neutral: "Neutral", frustrated: "Frustración expresada", unclear: "No observable",
};
export function stateLabel(value: string) { return labels[value] ?? value; }
