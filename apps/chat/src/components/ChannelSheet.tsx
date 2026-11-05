import { useEffect, useId, useRef, type ReactNode } from "react";
import { useI18n } from "../language";

export function ChannelIcon({ kind }: { kind: "phone" | "mic" | "close" | "link" }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind === "phone" ? <path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 2 .7 2.9a2 2 0 0 1-.5 2.1L8 10a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.5c.9.3 1.9.6 2.9.7A2 2 0 0 1 22 16.9Z" /> : kind === "mic" ? <><rect x="9" y="2" width="6" height="12" rx="3" /><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" /></> : kind === "link" ? <><path d="m10 13 4-4m-6 7-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2-1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" /></> : <path d="m6 6 12 12M6 18 18 6" />}</svg>;
}

export function ChannelSheet({ open, onClose, title, children, className = "" }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; className?: string;
}) {
  const { t } = useI18n();
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    else if (!open && el.open) el.close();
  }, [open]);
  return <dialog ref={dialog} className={`channel-sheet ${className}`} aria-labelledby={titleId} onCancel={e => { e.preventDefault(); e.stopPropagation(); onClose(); }} onClick={e => { if (e.target === e.currentTarget) { const rect = e.currentTarget.getBoundingClientRect(); if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) onClose(); } }}>
    <div className="sheet-grip" aria-hidden="true" />
    <header className="sheet-header"><h2 id={titleId}>{title}</h2><button type="button" className="sheet-close" aria-label={t.sheet.close} onClick={onClose}><ChannelIcon kind="close" /></button></header>
    <div className="sheet-content">{children}</div>
  </dialog>;
}
