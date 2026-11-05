import { useEffect, useState } from "react";
import type { components } from "@screening/api-client";
import { useI18n, useNotice } from "../language";

type Interview = components["schemas"]["CandidateInterviewOut"];

/** The candidate's own interview: details, calendar file and a way to change it.
 * The personal token travels only in POST bodies, never in a URL. */
export function InterviewCard({ sessionId, token, status, refreshKey }: { sessionId: string; token: string; status: string; refreshKey: number }) {
  const { t } = useI18n();
  const [interview, setInterview] = useState<Interview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useNotice();
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [retry, setRetry] = useState(0);
  const body = JSON.stringify({ token, session_id: sessionId });
  useEffect(() => {
    const abort = new AbortController();
    setLoading(true); setLoadFailed(false);
    fetch("/api/v1/chat/interview", { method: "POST", headers: { "Content-Type": "application/json" }, body, signal: abort.signal })
      .then(r => { if (!r.ok) throw new Error("interview"); return r.json(); })
      .then((data: { interview: Interview | null }) => { if (!abort.signal.aborted) setInterview(data.interview); })
      .catch(() => { if (!abort.signal.aborted) setLoadFailed(true); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [body, status, refreshKey, retry]);
  const loadNotice = loadFailed ? <div role="alert"><p>{t.interview.loadFailed}</p><button type="button" className="chat-meta-action" onClick={() => setRetry(value => value + 1)}>{t.interview.retry}</button></div>
    : loading ? <p role="status">{t.interview.loading}</p> : null;
  if (!interview) return loadNotice ? <section className="interview-card" aria-label={t.interview.title}>{loadNotice}</section> : null;
  const active = interview.status !== "cancelled";
  const when = new Intl.DateTimeFormat(t.locale, { timeZone: interview.timezone, weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }).format(new Date(interview.starts_at));
  async function download() {
    setError(null);
    try {
      const r = await fetch("/api/v1/chat/interview/ics", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      if (!r.ok) throw new Error("ics");
      const url = URL.createObjectURL(new Blob([await r.text()], { type: "text/calendar;charset=utf-8" }));
      const link = document.createElement("a");
      link.href = url; link.download = "entrevista.ics";
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setError(m => m.interview.downloadFailed); }
  }
  async function manage() {
    setBusy(true); setError(null);
    try {
      const r = await fetch("/api/v1/chat/interview/manage", { method: "POST", headers: { "Content-Type": "application/json" }, body });
      if (!r.ok) throw new Error("manage");
    } catch { setError(m => m.interview.manageFailed); }
    finally { setBusy(false); }
  }
  return <section className="interview-card" aria-label={t.interview.title}>
    {loadNotice}
    <div className="interview-card-heading"><strong>{t.interview.title}</strong>{interview.status !== "booked" && <span className="interview-state">{t.interview.states[interview.status] ?? interview.status}</span>}</div>
    <p className={active ? "" : "is-cancelled"}><time dateTime={interview.starts_at}>{when}</time></p>
    <p>{interview.site_name}{interview.address ? ` · ${interview.address}` : ""}</p>
    <p className="interview-note">{t.interview.duration(interview.duration_minutes)}</p>
    {active && status === "active" && interview.can_change && <p className="interview-note">{t.interview.manageHint}</p>}
    <div className="interview-actions">
      <button type="button" className="chat-meta-action" onClick={() => void download()}>{active ? t.interview.addToCalendar : t.interview.removeFromCalendar}</button>
      {interview.can_change && status === "completed" && <button type="button" className="chat-meta-action" disabled={busy} onClick={() => void manage()}>{busy ? t.interview.managing : t.interview.manage}</button>}
    </div>
    {error && <p role="alert" className="interview-error">{error(t)}</p>}
  </section>;
}
