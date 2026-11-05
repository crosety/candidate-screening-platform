import { useCallback, useEffect, useRef, useState } from "react";
import { api, type components } from "@screening/api-client";
import { ChannelSheet } from "./ChannelSheet";
import { channelFailure, normalizeMobile, validMobile } from "./candidateChannels";
import { errorText, LocalizedError } from "../i18n";
import { useI18n, useNotice } from "../language";

type Status = components["schemas"]["SmsStatusOut"];

export function SmsLinkControl({ sessionId, token }: { sessionId: string; token: string }) {
  const { t } = useI18n();
  const [info, setInfo] = useState<Status | null>(null);
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useNotice();
  const [statusError, setStatusError] = useNotice();
  const [open, setOpen] = useState(false);
  const requestId = useRef(crypto.randomUUID());
  const refresh = useCallback(async (signal?: AbortSignal) => {
    try {
      const { data } = await api.POST("/api/v1/chat/sms-link", { body: { session_id: sessionId, token, request_id: requestId.current, phone: "", consent: false, action: "status" }, signal });
      if (signal?.aborted) return;
      if (!data || !("available" in data)) throw new LocalizedError(m => m.sms.statusFailed);
      setInfo(data); setStatusError(null);
    } catch {
      if (!signal?.aborted) setStatusError(m => m.sms.statusFailed);
    }
  }, [sessionId, token, setStatusError]);
  useEffect(() => {
    if (!open) return;
    const abort = new AbortController();
    const update = () => { if (document.visibilityState !== "hidden") void refresh(abort.signal); };
    update(); const interval = window.setInterval(update, 60000);
    return () => { abort.abort(); window.clearInterval(interval); };
  }, [open, refresh]);
  async function act(action: "send" | "revoke") {
    setBusy(true); setError(null);
    try {
      const { data, error } = await api.POST("/api/v1/chat/sms-link", { body: { session_id: sessionId, token, request_id: requestId.current, phone: normalizeMobile(phone), consent, action } });
      if (!data) throw new LocalizedError(channelFailure(error, m => m.sms.confirmFailed));
      if (action === "revoke") { requestId.current = crypto.randomUUID(); setConsent(false); }
      await refresh();
    }
    catch (e) { setError(errorText(e, m => m.sms.requestFailed)); }
    finally { setBusy(false); }
  }
  const subscribed = info?.notifications.some(n => n.purpose === "invitation" && !n.revoked);
  const statusNotice = statusError && <div role="alert"><p>{statusError(t)}</p><button type="button" className="chat-meta-action" onClick={() => void refresh()}>{t.sms.retry}</button></div>;
  return <><button type="button" className="chat-meta-action" onClick={() => setOpen(true)}>{t.sms.action}</button><ChannelSheet open={open} onClose={() => setOpen(false)} title={t.sms.title}><div className="channel-form">
    <p>{t.sms.intro}</p>
    {!info && !statusError && <p role="status">{t.sms.checking}</p>}
    {statusNotice}
    {info && !info.available && <p role="status">{t.sms.unavailable}</p>}
    {info?.notifications.map(n => <p key={n.id} role="status">{n.purpose === "after_call" ? t.sms.purposeAfterCall : t.sms.purposeLink}: {t.sms.states[n.state] || t.sms.statePending}{n.revoked ? ` · ${t.sms.alertsOff}` : ""}</p>)}
    {!subscribed && info?.available && !statusError && <><label className="channel-field">{t.sheet.mobileLabel}<input type="tel" inputMode="tel" autoComplete="tel" placeholder="+34 600 000 000" value={phone} onChange={e => setPhone(e.target.value)} /></label><label className="channel-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} /><span>{t.sms.consent}</span></label><button type="button" className="sheet-primary" disabled={busy || !consent || !validMobile(phone)} onClick={() => void act("send")}>{busy ? t.sheet.requesting : t.sms.send}</button></>}
    {subscribed && <button type="button" className="sheet-secondary" disabled={busy} onClick={() => void act("revoke")}>{t.sms.revoke}</button>}
    {error && <p role="alert">{error(t)}</p>}
  </div></ChannelSheet></>;
}
