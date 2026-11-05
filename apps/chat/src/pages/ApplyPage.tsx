import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router";
import { api } from "@screening/api-client";
import { PrivacyNotice } from "../components/PrivacyNotice";
import { errorText, LocalizedError } from "../i18n";
import { LanguageSwitch, useI18n, useNotice } from "../language";

export function ApplyPage() {
  const { tenantSlug = "", offerKey = "" } = useParams();
  const navigate = useNavigate();
  const { t } = useI18n();
  const [requestId] = useState(() => crypto.randomUUID());
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useNotice();
  const offer = useQuery({ queryKey: ["public-offer", tenantSlug, offerKey], queryFn: async () => {
    const result = await api.GET("/api/v1/apply/{tenant_slug}/{offer_key}", { params: { path: { tenant_slug: tenantSlug, offer_key: offerKey } } });
    if (!result.data) throw new LocalizedError(result.response.status === 410 ? m => m.apply.offerClosed : m => m.apply.offerUnavailable);
    return result.data;
  }});
  useEffect(() => { document.title = offer.data ? `${offer.data.title} · ${offer.data.company}` : t.apply.documentTitle; }, [offer.data, t]);
  async function start() {
    if (busy) return;
    setBusy(true); setError(null);
    try {
      const result = await api.POST("/api/v1/apply/{tenant_slug}/{offer_key}", { params: { path: { tenant_slug: tenantSlug, offer_key: offerKey } }, body: { request_id: requestId, name: name.trim(), phone: phone.trim(), consent } });
      if (!result.data) throw new LocalizedError(result.response.status === 410 ? m => m.apply.startClosed : m => m.apply.startFailed);
      const destination = new URL(result.data.candidate_link);
      navigate(destination.pathname);
    } catch (cause) { setError(errorText(cause, m => m.apply.startFailed)); setBusy(false); }
  }
  if (offer.error) return <main className="apply-page"><header className="apply-brand apply-brand-compact"><LanguageSwitch /></header><p role="alert">{errorText(offer.error, m => m.apply.offerUnavailable)(t)}</p><button className="apply-start" onClick={() => void offer.refetch()}>{t.apply.retry}</button></main>;
  if (!offer.data) return <main className="apply-page"><p role="status">{t.apply.loading}</p></main>;
  return <main className="apply-page"><header className="apply-brand"><span>{offer.data.company}</span><span className="apply-brand-side"><span className="apply-brand-tag">{t.apply.brandTag}</span><LanguageSwitch /></span></header><section className="apply-offer"><p className="apply-eyebrow">{t.apply.eyebrow}</p><h1>{offer.data.title}</h1><p className="apply-description">{offer.data.description}</p><div className="apply-details">{Object.entries(offer.data.dimensions).map(([key, value]) => <span key={key}>{key}: {value}</span>)}</div></section><section className="apply-form"><h2>{t.apply.formTitle}</h2><p>{t.apply.formIntro}</p><form onSubmit={e => { e.preventDefault(); void start(); }}><label>{t.apply.nameLabel}<input autoComplete="name" required minLength={2} maxLength={200} value={name} onChange={e => setName(e.target.value)} disabled={busy} /></label><label>{t.apply.phoneLabel} <small>{t.apply.optional}</small><input type="tel" autoComplete="tel" maxLength={30} pattern="[+0-9 ()\-]*" value={phone} onChange={e => setPhone(e.target.value)} disabled={busy} /><small>{t.apply.phoneHelp}</small></label><PrivacyNotice /><label className="apply-consent"><input type="checkbox" checked={consent} onChange={e => setConsent(e.target.checked)} required disabled={busy} /><span>{t.apply.consent}</span></label>{error && <p role="alert" className="apply-error">{error(t)}</p>}<button className="apply-start" disabled={busy || !consent || name.trim().length < 2}>{busy ? t.apply.starting : t.apply.start}</button></form></section><footer>{t.apply.footer}</footer></main>;
}
