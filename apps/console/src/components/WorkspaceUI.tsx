import type { ReactNode } from "react";
import { ArrowUpRight, Circle, CircleCheck, CircleDashed, Inbox, Users, type LucideIcon } from "lucide-react";
import { stateLabel } from "@/lib/states";

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow?: string; title: string; description: string; actions?: ReactNode }) {
  return <header className="page-heading"><div>{eyebrow && <p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1><p className="page-description">{description}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}
export function Avatar({ name, small = false }: { name: string; small?: boolean }) {
  const initials = name.trim().split(/\s+/).slice(0, 2).map(s => s[0]).join("").toUpperCase();
  return <span aria-hidden="true" className={`person-avatar ${small ? "person-avatar-small" : ""}`}>{initials || "—"}</span>;
}
export function StatusBadge({ status }: { status: string }) {
  const Icon = ["completed", "qualified", "done"].includes(status) ? CircleCheck : status === "active" ? Circle : CircleDashed;
  return <span className={`status-badge status-${status}`}><Icon size={12} aria-hidden="true" />{stateLabel(status)}</span>;
}
export function EmptyState({ title, description, compact = false }: { title: string; description: string; compact?: boolean }) {
  return <div className={`empty-state ${compact ? "empty-state-compact" : ""}`}><span className="empty-icon"><Inbox size={22} aria-hidden="true" /></span><h3>{title}</h3><p>{description}</p></div>;
}
export function MetricCard({ label, value, description, kind = "total", icon }: { label: string; value: number | string | undefined; description: string; kind?: string; icon?: LucideIcon }) {
  const Icon = icon ?? (kind === "active" ? CircleDashed : kind === "completed" ? CircleCheck : kind === "handed_off" ? ArrowUpRight : Users);
  return <div className={`metric-card ${kind === "total" ? "metric-featured" : ""}`}><div className="metric-label">{label}<Icon size={16} aria-hidden="true" /></div><div className="metric-value">{value === undefined ? <span className="skeleton-value" /> : value.toLocaleString("es")}</div><p>{description}</p></div>;
}
