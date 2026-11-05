import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { api, type components } from "@screening/api-client";
import { useChatSocket } from "../hooks/useChatSocket";
import { VoiceControl } from "../components/VoiceControl";
import { TelephoneControl } from "../components/TelephoneControl";
import { SmsLinkControl } from "../components/SmsLinkControl";
import { PrivacyNotice } from "../components/PrivacyNotice";
import { InterviewCard } from "../components/InterviewCard";
import { LocalizedError } from "../i18n";
import { LanguageSwitch, useI18n } from "../language";

type OpenedSession = components["schemas"]["OpenedSessionOut"];

function ChatIcon({ kind }: { kind: "message" | "arrow" | "check" | "shield" | "chevron" }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{kind === "message" ? <path d="M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9H13a8.5 8.5 0 0 1 8 8v.5Z" /> : kind === "arrow" ? <path d="M12 19V5m-6 6 6-6 6 6" /> : kind === "check" ? <path d="m5 12 4 4L19 6" /> : kind === "chevron" ? <path d="m7 10 5 5 5-5" /> : <><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z" /><path d="m8 12 3 3 5-6" /></>}</svg>;
}

function Conversation({ session, token }: { session: OpenedSession; token: string }) {
  const { t } = useI18n();
  const { messages, status, typing, connection, error, send, awaitingOpening, telephoneRequest } = useChatSocket(session.session_id, token, session.status);
  const [text, setText] = useState("");
  const [voiceActive, setVoiceActive] = useState(false);
  const [telephoneActive, setTelephoneActive] = useState(false);
  const transcript = useRef<HTMLElement>(null);
  const followMessages = useRef(true);
  const [newMessages, setNewMessages] = useState(false);
  const disabled = !["active", "handed_off"].includes(status) || connection !== "open" || typing || voiceActive || telephoneActive || awaitingOpening;
  const ended = !["active", "handed_off"].includes(status);
  const statusText = status === "handed_off" ? t.chat.statusHandedOff : status === "completed" ? t.chat.statusCompleted : ended ? t.chat.statusClosed : t.chat.statusActive;
  useEffect(() => { document.title = t.chat.documentTitle(session.tenant_name); }, [session.tenant_name, t]);
  useEffect(() => {
    if (followMessages.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
    else if (messages.length) setNewMessages(true);
  }, [messages, typing]);
  function submit() { if (disabled) return; followMessages.current = true; if (send(text)) setText(""); }
  return <main className="candidate-experience"><aside className="candidate-context" aria-label={t.chat.contextLabel}><div className="candidate-wordmark">orbio<span>.</span></div><div className="candidate-context-body"><p className="context-eyebrow">{session.tenant_name}</p><h2>{ended ? t.chat.headingEnded : t.chat.headingActive}</h2><p>{ended ? t.chat.introEnded : t.chat.introActive}</p><div className="candidate-process"><span className="context-step-icon"><ChatIcon kind={ended ? "check" : "message"} /></span><div><strong>{statusText}</strong><p>{ended ? t.chat.processEnded : t.chat.processActive}</p></div></div></div><div className="candidate-context-footer"><ChatIcon kind="shield" /><p>{t.chat.personalLink}<br />{t.chat.keepLink}</p></div></aside>
  <div className="candidate-chat">
    <header className="chat-header"><span className="chat-brand-mark" aria-hidden="true">{session.tenant_name.slice(0, 1)}</span><div className="chat-heading"><p className="chat-eyebrow">{session.tenant_name}</p><h1>{session.offer_title || t.chat.offerFallback}</h1><div className="chat-presence" role="status"><span className={`presence-dot ${connection === "open" ? "is-connected" : ""}`} /><span>{ended ? statusText : status === "handed_off" ? t.chat.presenceHandedOff : `${t.chat.presenceAssistant} · ${connection === "open" ? t.chat.online : connection === "connecting" ? t.chat.connecting : t.chat.offline}`}</span></div></div><span className="chat-private"><ChatIcon kind="shield" />{t.chat.privateLink}</span><LanguageSwitch className="chat-language" /></header>
    <div className="chat-transcript-region" hidden={voiceActive}><section ref={transcript} className="chat-transcript" role="log" aria-label={t.chat.transcriptLabel} aria-relevant="additions text" aria-live={voiceActive ? "off" : "polite"} onScroll={e => { const el = e.currentTarget; followMessages.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80; if (followMessages.current) setNewMessages(false); }}><p className="chat-introduction">{t.chat.introduction(session.tenant_name)}</p>
      {awaitingOpening && <div className="chat-welcome" role="status"><span className="welcome-icon"><ChatIcon kind="message" /></span><h2>{error ? t.chat.openingFailedTitle : t.chat.openingTitle}</h2><p>{error ? t.chat.openingFailedText : t.chat.openingText}</p></div>}
      {messages.map(message => <div key={message.id} className={`chat-message ${message.direction === "in" ? "is-inbound" : "is-outbound"}`}><div className="chat-message-bubble"><span className="message-author">{message.direction === "in" ? t.chat.authorYou : message.channel === "human" ? t.chat.authorTeam : t.chat.authorAssistant}</span><p className="whitespace-pre-wrap break-words">{message.text}</p><p className="message-time"><time dateTime={message.created_at}>{new Date(message.created_at).toLocaleTimeString(t.locale, { hour: "2-digit", minute: "2-digit" })}</time>{message.pending ? ` · ${t.chat.sending}` : ""}</p></div></div>)}
      {typing && <p className="chat-typing" role="status"><span /><span /><span />{t.chat.typing}</p>}
    </section>{newMessages && <button className="new-messages" onClick={() => { if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight; followMessages.current = true; setNewMessages(false); }}>{t.chat.newMessages} <ChatIcon kind="chevron" /></button>}</div>
    <footer className={`chat-composer space-y-3 ${voiceActive ? "is-voice-mode" : ""}`}>
      {error && !voiceActive && <div role="alert" className="chat-error"><p>{error(t)}</p>{(connection !== "open" || awaitingOpening) && <button onClick={() => window.location.reload()}>{t.chat.reconnect}</button>}</div>}
      {status === "handed_off" && !voiceActive && <div role="status" className="chat-complete"><div><strong>{t.chat.handoffTitle}</strong><p>{t.chat.handoffText}</p></div></div>}
      {ended && !voiceActive && <div role="status" className="chat-complete"><span><ChatIcon kind={status === "handed_off" ? "message" : "check"} /></span><div><strong>{statusText}</strong><p>{status === "handed_off" ? t.chat.endedHandedOff : t.chat.endedHistory}</p></div></div>}
      {!voiceActive && <InterviewCard sessionId={session.session_id} token={token} status={status} refreshKey={messages.length} />}
      {!ended && <form hidden={voiceActive} className="chat-input-container" onSubmit={e => { e.preventDefault(); submit(); }}><textarea rows={2} maxLength={2000} aria-label={t.chat.messageLabel} placeholder={typing ? t.chat.placeholderWaiting : t.chat.placeholder} value={text} onChange={e => setText(e.target.value)} disabled={disabled} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }} /><button type="submit" disabled={disabled || !text.trim()} aria-label={t.chat.send}><ChatIcon kind="arrow" /></button></form>}
      <div className="channel-actions">
        {(status === "active" || voiceActive) && <VoiceControl sessionId={session.session_id} token={token} disabled={status !== "active" || awaitingOpening || telephoneActive || connection !== "open"} processing={typing} onActiveChange={setVoiceActive} />}
        {!voiceActive && <TelephoneControl sessionId={session.session_id} token={token} disabled={status !== "active" || typing || awaitingOpening || voiceActive} onActiveChange={setTelephoneActive} requested={telephoneRequest} hideWhenIdle={status !== "active"} />}
      </div>
      {!voiceActive && <div className="candidate-meta"><PrivacyNotice /><SmsLinkControl sessionId={session.session_id} token={token} /></div>}
    </footer>
  </div></main>;
}

export function ChatPage() {
  const { token = "" } = useParams();
  const { t, explicitLanguage, fallbackLanguage, setSessionLanguage } = useI18n();
  const session = useQuery({ queryKey: ["chat-session", token], queryFn: async () => {
    const result = await api.POST("/api/v1/chat/sessions", { body: { token, language: explicitLanguage ?? "", fallback_language: fallbackLanguage } });
    if (!result.data) throw result.response.status === 404 ? new LocalizedError(m => m.access.invalidLink, "invalid_link") : new LocalizedError(m => m.access.openFailed);
    return result.data;
  }});
  const sessionLanguage = session.data?.language ?? "";
  useEffect(() => { setSessionLanguage(sessionLanguage); return () => setSessionLanguage(""); }, [sessionLanguage, setSessionLanguage]);
  useEffect(() => { if (!session.data) document.title = t.app.title; }, [session.data, t]);
  if (session.isError) {
    const failure = session.error instanceof LocalizedError ? session.error : new LocalizedError(m => m.access.openFailed);
    return <main className="chat-access-state"><span className="welcome-icon"><ChatIcon kind="message" /></span><p className="chat-eyebrow">{t.access.eyebrow}</p><h1>{t.access.errorTitle}</h1><p role="alert">{failure.text(t)}</p>{failure.code === "invalid_link" ? <p>{t.access.invalidLinkHelp}</p> : <button onClick={() => void session.refetch()}>{t.access.retry}</button>}<LanguageSwitch className="access-language" /></main>;
  }
  if (!session.data) return <main className="chat-access-state" role="status"><span className="chat-loader" /><h1>{t.access.loadingTitle}</h1><p>{t.access.loadingText}</p></main>;
  return <Conversation key={session.data.session_id} session={session.data} token={token} />;
}
