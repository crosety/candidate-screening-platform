import { useCallback, useEffect, useRef, useState } from "react";
import { api, type components } from "@screening/api-client";
import { ChannelIcon, ChannelSheet } from "./ChannelSheet";
import { PrivacyNotice } from "./PrivacyNotice";
import { channelFailure, channelMessage, normalizeMobile, validMobile } from "./candidateChannels";
import { errorText, LocalizedError } from "../i18n";
import { useI18n, useNotice } from "../language";

type Status = components["schemas"]["TelephoneStatusOut"];
const activeStates = new Set(["queued", "dialing", "ringing", "in-progress", "unknown"]);

export function TelephoneControl({ sessionId, token, disabled, onActiveChange, requested = 0, hideWhenIdle = false }: {
  sessionId: string; token: string; disabled: boolean; onActiveChange: (active: boolean) => void; requested?: number; hideWhenIdle?: boolean;
}) {
  const { t } = useI18n();
  const [state, setState] = useState<Status | null>(null);
  const [phone, setPhone] = useState("");
  const [retry, setRetry] = useState(false);
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);
  const [error, setError] = useNotice();
  const [statusError, setStatusError] = useNotice();
  const requestId = useRef(crypto.randomUUID());
  const active = !!state?.attempt && activeStates.has(state.attempt.state);
  const normalizedPhone = normalizeMobile(phone);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const r = await api.POST("/api/v1/chat/telephone/status", { body: { session_id: sessionId, token }, signal });
    if (signal?.aborted) return;
    if (!r.data) throw new LocalizedError(m => m.telephone.statusFailed);
    setState(r.data);
    setStatusError(null);
  }, [sessionId, token, setStatusError]);
  useEffect(() => {
    const abort = new AbortController();
    let checked = false;
    const update = () => { if (document.visibilityState === "hidden") return; checked = true; void refresh(abort.signal).catch(e => { if (!abort.signal.aborted) setStatusError(errorText(e, m => m.telephone.statusFailed)); }); };
    const onVisible = () => { if (!checked || active || open) update(); };
    update();
    // Restore a running call on mount, then poll only while it matters.
    const interval = active || open ? window.setInterval(update, active ? 5000 : 60000) : undefined;
    document.addEventListener("visibilitychange", onVisible);
    return () => { abort.abort(); window.clearInterval(interval); document.removeEventListener("visibilitychange", onVisible); };
  }, [refresh, active, open, setStatusError]);
  useEffect(() => { onActiveChange(active && state?.attempt?.state !== "unknown"); }, [active, state?.attempt?.state, onActiveChange]);
  useEffect(() => { if (requested) setOpen(true); }, [requested]);
  function close() { setOpen(false); setConsent(false); }
  async function submit() {
    if (busy || disabled || !consent || !validMobile(normalizedPhone)) return;
    setBusy(true); setError(null);
    try {
      const { data, error, response } = await api.POST("/api/v1/chat/telephone/request", { body: { session_id: sessionId, token, request_id: requestId.current, phone: normalizedPhone, consent } });
      if (!data) throw new LocalizedError([400, 403, 409, 422, 429].includes(response.status) ? channelFailure(error, m => m.telephone.requestUnavailable) : m => m.telephone.requestUnconfirmed);
      setState(current => current ? { ...current, attempt: data } : current);
      setRetry(false); setConsent(false); setOpen(false);
    } catch (e) { setError(errorText(e, m => m.telephone.requestFailed)); }
    finally { setBusy(false); }
  }
  if (hideWhenIdle && !active) return null;
  const attemptLabel = state?.attempt ? t.telephone.states[state.attempt.state] || t.telephone.checking : "";
  const notice = error ?? statusError;
  return <div className="telephone-channel">
    <button type="button" className="channel-action" disabled={busy || (disabled && !active)} onClick={() => setOpen(true)}><ChannelIcon kind="phone" /><span>{active ? t.telephone.viewCall : t.telephone.action}</span></button>
    {state?.attempt && <button type="button" className={`telephone-status ${active ? "is-active" : ""}`} onClick={() => setOpen(true)}><span className="call-status-dot" /><span role="status">{attemptLabel}</span></button>}
    <ChannelSheet open={open} onClose={close} title={active ? t.telephone.titleActive : t.telephone.titleIdle}>
      <span className="sheet-channel-icon" aria-hidden="true"><ChannelIcon kind="phone" /></span>
      {state?.attempt && !retry && <p className="sheet-status" role="status">{attemptLabel}</p>}
      {!state && !notice && <p role="status">{t.telephone.checkingAvailability}</p>}
      {state && !state.available && <p role="status">{channelMessage(state.reason, m => m.telephone.unavailable)(t)}</p>}
      {active && <><p>{t.telephone.activeNote}</p><button type="button" className="sheet-primary" onClick={close}>{t.sheet.backToChat}</button></>}
      {state?.attempt && !active && state.available && !retry && <button type="button" className="sheet-primary" disabled={disabled} onClick={() => { requestId.current = crypto.randomUUID(); setConsent(false); setError(null); setRetry(true); }}>{t.telephone.requestAnother}</button>}
      {state?.available && (!state.attempt || retry) && <form className="channel-form" onSubmit={e => { e.preventDefault(); void submit(); }}>
        <p>{t.telephone.intro}</p>
        <label className="channel-field">{t.sheet.mobileLabel}<input autoFocus type="tel" inputMode="tel" autoComplete="tel" placeholder="+34 600 000 000" maxLength={24} value={phone} onChange={e => setPhone(e.target.value)} disabled={busy || disabled} /></label>
        <label className="channel-consent"><input type="checkbox" checked={consent} disabled={busy || disabled} onChange={e => setConsent(e.target.checked)} /><span>{t.telephone.consent}</span></label>
        <button type="submit" className="sheet-primary" disabled={busy || disabled || !consent || !validMobile(normalizedPhone)}>{busy ? t.sheet.requesting : t.telephone.callNow}</button>
        <p className="sheet-footnote">{t.telephone.footnote}</p>
      </form>}
      {notice && <p role="alert" className="sheet-error">{notice(t)}</p>}
      {statusError && <button type="button" className="sheet-secondary" onClick={() => void refresh().catch(e => setStatusError(errorText(e, m => m.telephone.statusFailed)))}>{t.telephone.retry}</button>}
      <PrivacyNotice />
    </ChannelSheet>
  </div>;
}
