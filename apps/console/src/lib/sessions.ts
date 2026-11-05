export const sessionLabels = { active: "En curso", handed_off: "Derivada", completed: "Terminada", abandoned: "Abandonada" };
export const capabilityColors = { ok: "bg-emerald-50 text-emerald-800", error: "bg-red-50 text-red-800", blocked: "bg-amber-50 text-amber-800" };
export function cost(micros: number) { return `$${(micros / 1_000_000).toFixed(4)}`; }
