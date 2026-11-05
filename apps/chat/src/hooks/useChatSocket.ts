import { useCallback, useEffect, useRef, useState } from "react";
import { api, type components } from "@screening/api-client";
import { serverText, type Localized } from "../i18n";
import { useI18n, useNotice } from "../language";

type Status = components["schemas"]["SessionOut"]["status"];
export type ChatMessage = components["schemas"]["MessageOut"] & { pending?: boolean };
type ServerEvent =
  | { type: "history"; messages: ChatMessage[]; status: Status }
  | { type: "message"; message: ChatMessage }
  | { type: "typing"; on: boolean }
  | { type: "status"; status: Status }
  | { type: "telephone_request" }
  | { type: "error"; code: string; message: string }
  | { type: "pong" };

/** Socket errors carry a stable code; their Spanish text is shown verbatim only in Spanish. */
function serverError(code: string, message: string): Localized {
  return serverText(message, m => m.connection.serverErrors[code] ?? m.connection.serverErrors.default);
}

export function useChatSocket(sessionId: string, token: string, initialStatus: Status) {
  const { explicitLanguage, fallbackLanguage } = useI18n();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [status, setStatus] = useState<Status>(initialStatus);
  const [typing, setTyping] = useState(false);
  const [connection, setConnection] = useState<"connecting" | "open" | "closed">("connecting");
  const [error, setError] = useNotice();
  const [telephoneRequest, setTelephoneRequest] = useState(0);
  const socket = useRef<WebSocket | null>(null);
  const awaitingOpening = status === "active" && messages.length === 0;

  useEffect(() => {
    let disposed = false;
    let retries = 0;
    let timer: number | undefined;
    async function connect() {
      if (disposed) return;
      setConnection("connecting");
      let ticket: string;
      try {
        const opened = await api.POST("/api/v1/chat/sessions", { body: { token, language: explicitLanguage ?? "", fallback_language: fallbackLanguage } });
        if (!opened.data || opened.data.session_id !== sessionId) throw new Error("invalid_link");
        ticket = opened.data.websocket_token;
      } catch {
        if (!disposed) { setConnection("closed"); setError(m => m.connection.renewFailed); }
        return;
      }
      if (disposed) return;
      const protocol = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${protocol}://${location.host}/ws/chat/${sessionId}/?ticket=${encodeURIComponent(ticket)}`);
      socket.current = ws;
      ws.onopen = () => { if (!disposed) { setConnection("open"); setError(null); retries = 0; } };
      ws.onmessage = event => {
        if (disposed) return;
        try {
          const data = JSON.parse(event.data) as ServerEvent;
          if (data.type === "history") { setMessages(data.messages); setStatus(data.status); setTyping(false); }
          else if (data.type === "message") {
            setMessages(current => {
              const acknowledged = current.filter(m => m.id !== data.message.client_message_id).map(m => ({ ...m, pending: false }));
              return acknowledged.some(m => m.id === data.message.id) ? acknowledged : [...acknowledged, data.message];
            });
          }
          else if (data.type === "typing") setTyping(data.on);
          else if (data.type === "status") setStatus(data.status);
          else if (data.type === "telephone_request") setTelephoneRequest(n => n + 1);
          else if (data.type === "error") {
            setError(serverError(data.code, data.message));
            if (data.code === "session_closed") setStatus(current => current === "active" ? "completed" : current);
          }
        } catch { setError(m => m.connection.unreadable); }
      };
      ws.onclose = event => {
        if (disposed) return;
        setConnection("closed"); setTyping(false);
        if (event.code === 4403) { setError(m => m.connection.invalidLink); return; }
        if (retries < 5) {
          const delay = [1000, 2000, 5000][Math.min(retries, 2)];
          retries += 1;
          timer = window.setTimeout(connect, delay);
        } else setError(m => m.connection.lost);
      };
      ws.onerror = () => { if (!disposed) setConnection("closed"); };
    }
    connect();
    return () => { disposed = true; window.clearTimeout(timer); socket.current?.close(); socket.current = null; };
  }, [sessionId, token, setError, explicitLanguage, fallbackLanguage]);

  const send = useCallback((text: string): boolean => {
    const trimmed = text.trim();
    if (!trimmed || trimmed.length > 2000 || !["active", "handed_off"].includes(status) || socket.current?.readyState !== WebSocket.OPEN || typing || awaitingOpening) return false;
    const id = crypto.randomUUID();
    socket.current.send(JSON.stringify({ type: "message", client_message_id: id, text: trimmed }));
    setMessages(current => [...current, { id, direction: "in", text: trimmed, created_at: new Date().toISOString(), channel: "web", client_message_id: id, pending: true }]);
    setTyping(true); setError(null);
    return true;
  }, [status, typing, awaitingOpening, setError]);
  return { messages, status, typing, connection, error, send, awaitingOpening, telephoneRequest };
}
