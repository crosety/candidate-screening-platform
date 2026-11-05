import type { CapabilityId, ProfileContent } from "./profile";

// Mirrors the capability needs_kinds catalog in docs/arquitectura.md §10.
export const capabilityCatalog: { id: CapabilityId; needs_kinds: NonNullable<ProfileContent["connections"]>[number]["kind"][] }[] = [
  { id: "openings.find", needs_kinds: ["data"] },
  { id: "application.read_mine", needs_kinds: ["ats"] },
  { id: "interview.book", needs_kinds: ["calendar"] },
  { id: "handoff.request", needs_kinds: [] },
  { id: "credential.verify", needs_kinds: ["registry"] },
  { id: "followup.schedule", needs_kinds: [] },
  { id: "candidates.query", needs_kinds: [] },
  { id: "analytics.aggregates", needs_kinds: [] },
];

export function profileId(name: string, existing: string[], fallback: "fuente" | "conexion") {
  const base = name.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9_-]/g, "_").replace(/_+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40) || fallback;
  let id = base;
  for (let n = 2; existing.includes(id); n++) { const suffix = `_${n}`; id = base.slice(0, 40 - suffix.length) + suffix; }
  return id;
}
