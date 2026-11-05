import { useEffect, useRef, useState } from "react";
import { api } from "@screening/api-client";
import { voiceCloseMessage, voiceStartMessage } from "./voiceErrors";
import { VoiceTurn, type VoicePhase } from "./voiceTurn";
import { ChannelIcon, ChannelSheet } from "./ChannelSheet";
import { PrivacyNotice } from "./PrivacyNotice";
import { errorText, LocalizedError, serverText, type Messages } from "../i18n";
import { useI18n, useNotice } from "../language";

function encode(samples: Float32Array, rate: number): string {
  const count = Math.floor(samples.length * 16000 / rate);
  const bytes = new Uint8Array(count * 2);
  const view = new DataView(bytes.buffer);
  for (let i = 0; i < count; i++) {
    const sample = Math.max(-1, Math.min(1, samples[Math.floor(i * rate / 16000)] ?? 0));
    view.setInt16(i * 2, sample * (sample < 0 ? 32768 : 32767), true);
  }
  return btoa(String.fromCharCode(...bytes));
}

export function VoiceControl({ sessionId, token, disabled, processing, onActiveChange }: { sessionId: string; token: string; disabled: boolean; processing: boolean; onActiveChange: (active: boolean) => void }) {
  const { t } = useI18n();
  const [active, setActive] = useState(false);
  const [panelOpen, setPanelOpen] = useState(false);
  const [consent, setConsent] = useState(false);
  const [error, setError] = useNotice();
  const [voiceInfo, setVoiceInfo] = useState<{ provider: string; transport: string } | null>(null);
  const isLive = ["gpt_live", "eleven_direct"].includes(voiceInfo?.transport ?? "");
  const [phase, setPhase] = useState<VoicePhase>("connecting");
  const closed = useRef(disabled);
  closed.current = disabled;
  const cleanup = useRef<() => void>(() => {});
  const generation = useRef(0);
  const processingHandler = useRef<() => void>(() => {});
  useEffect(() => { if (processing) processingHandler.current(); }, [processing]);
  function stop() { generation.current++; cleanup.current(); cleanup.current = () => {}; setActive(false); setPanelOpen(false); onActiveChange(false); }
  useEffect(() => () => { generation.current++; cleanup.current(); }, []);
  useEffect(() => {
    if (!panelOpen) return;
    let cancelled = false;
    setConsent(false); setVoiceInfo(null);
    void api.POST("/api/v1/chat/sessions/{session_id}/voice/config", {
      params: { path: { session_id: sessionId } }, body: { token, consent: false, provider: "" },
    }).then(result => {
      if (cancelled) return;
      if (result.data) setVoiceInfo(result.data);
      else setError(m => m.voice.configFailed);
    }).catch(() => { if (!cancelled) setError(m => m.voice.unavailableNow); });
    return () => { cancelled = true; };
  }, [sessionId, token, panelOpen, setError]);
  async function start() {
    const turn = ++generation.current;
    setError(null); setPhase("connecting"); setActive(true); onActiveChange(true);
    let stream: MediaStream | undefined;
    let socket: WebSocket | undefined;
    let audio: AudioContext | undefined;
    let processor: ScriptProcessorNode | undefined;
    let settleTimer: number | undefined;
    let responseTimer: number | undefined;
    let providerClosed = false;
    let live = false;
    let backendBusy = false;
    const voiceTurn = new VoiceTurn();
    const playing = new Set<AudioBufferSourceNode>();
    let cleaned = false;
    cleanup.current = () => { if (cleaned) return; cleaned = true; processingHandler.current = () => {}; window.clearTimeout(settleTimer); window.clearTimeout(responseTimer); processor?.disconnect(); stream?.getTracks().forEach(t => t.stop()); socket?.close(); playing.forEach(s => { s.onended = null; s.stop(); }); playing.clear(); if (audio && audio.state !== "closed") void audio.close().catch(() => {}); };
    function beginTurn() {
      window.clearTimeout(settleTimer);
      if (!voiceTurn.waiting) {
        voiceTurn.begin();
        // A stalled provider must not leave the microphone paused indefinitely.
        responseTimer = window.setTimeout(() => {
          if (generation.current !== turn) return;
          setError(m => m.voice.stalled);
          stop();
        }, 90000);
      }
      setPhase(voiceTurn.phase);
    }
    processingHandler.current = () => { if (generation.current === turn) beginTurn(); };
    function settlePlayback() {
      window.clearTimeout(settleTimer);
      setPhase(voiceTurn.phase);
      if (!voiceTurn.answered || voiceTurn.playing) return;
      // Allow trailing PCM chunks to arrive after the response event. Each
      // additional chunk cancels this timer; receipt alone is not playback.
      settleTimer = window.setTimeout(() => {
        if (generation.current !== turn || !voiceTurn.settle()) return;
        window.clearTimeout(responseTimer);
        if (closed.current || providerClosed) stop();
        else setPhase("listening");
      }, 750);
    }
    try {
      const result = await api.POST("/api/v1/chat/sessions/{session_id}/voice", { params: { path: { session_id: sessionId } }, body: { token, consent, provider: voiceInfo?.provider ?? "" } });
      if (!result.data) throw new LocalizedError(voiceStartMessage(result.response.status, result.error));
      if (generation.current !== turn) return;
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (generation.current !== turn) { stream.getTracks().forEach(t => t.stop()); return; }
      audio = new AudioContext();
      await audio.resume();
      const context = audio;
      let next = 0;
      let audibleUntil = 0;
      live = ["gpt_live", "eleven_direct"].includes(result.data.transport);
      if (live) processingHandler.current = () => {};
      const url = new URL(result.data.signed_url, window.location.href);
      if (live) url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(url.href);
      const ws = socket;
      const sessionToken = result.data.session_token;
      const transport = result.data.transport;
      ws.onopen = () => { if (!live) ws.send(JSON.stringify({ type: "conversation_initiation_client_data", ...(transport === "custom_llm" ? { custom_llm_extra_body: { session_token: sessionToken } } : { dynamic_variables: { orbio_session_token: sessionToken } }) })); };
      // The close event carries the provider's useful reason. Do not discard it
      // by advancing the generation on the preceding generic error event.
      ws.onerror = () => { if (generation.current === turn) setError(m => m.voice.connectFailed); };
      ws.onclose = event => {
        if (generation.current !== turn) return;
        const message = live ? (event.code === 1000 ? null : (m: Messages) => m.voice.liveClosed) : voiceCloseMessage(event.code, event.reason);
        if (message) setError(message);
        // A normal provider close can precede the end of locally buffered PCM.
        // Preserve the last answer instead of truncating it during cleanup.
        if (event.code === 1000 && (live || voiceTurn.answered) && playing.size > 0) {
          providerClosed = true;
          processor?.disconnect(); stream?.getTracks().forEach(t => t.stop());
          return;
        }
        if (!message && voiceTurn.waiting && !voiceTurn.answered) setError(m => m.voice.closedEarly);
        stop();
      };
      ws.onmessage = event => {
        if (generation.current !== turn) return;
        try {
          const data = JSON.parse(event.data as string);
          if (live && data.type === "error") { setError(serverText(data.message, m => m.voice.liveFailed)); stop(); return; }
          if (live && data.type === "closed" && !["close_requested", "expired", "remote_hangup"].includes(data.reason)) setError(m => m.voice.interrupted);
          if (live && data.type === "backend") {
            backendBusy = data.busy === true;
            if (context.currentTime >= audibleUntil) setPhase(backendBusy ? "thinking" : "listening");
          }
          if (live && data.type === "user_speaking") {
            playing.forEach(s => { s.onended = null; s.stop(); }); playing.clear(); next = 0;
            audibleUntil = 0; window.clearTimeout(settleTimer);
            setPhase("listening");
          }
          if (data.type === "ping") ws.send(JSON.stringify({ type: "pong", event_id: data.ping_event.event_id }));
          if (data.type === "conversation_initiation_metadata" || (live && data.type === "ready")) {
            if (processor) return;
            if (live) { if (data.sample_rate !== 16000) throw new Error("unsupported_sample_rate"); }
            else {
              const meta = data.conversation_initiation_metadata_event;
              if (meta.user_input_audio_format !== "pcm_16000" || meta.agent_output_audio_format !== "pcm_16000") throw new Error("unsupported_audio_format");
            }
            const source = context.createMediaStreamSource(stream!);
            // Native PCM capture avoids shipping provider SDK credentials or another dependency.
            processor = context.createScriptProcessor(4096, 1, 1);
            processor.onaudioprocess = e => {
              if (ws.readyState !== WebSocket.OPEN) return;
              const input = e.inputBuffer.getChannelData(0);
              // Pause capture until the guarded answer and its playback finish;
              // ambient speech must not enqueue turns while Orbio is processing.
              const samples = (!live && voiceTurn.waiting) || closed.current ? new Float32Array(input.length) : input;
              // Live keeps capturing during backend reasoning and speech; no input gate.
              if (ws.bufferedAmount > 64000) { setError(m => m.voice.tooSlow); stop(); return; }
              ws.send(JSON.stringify(live ? { type: "audio", audio: encode(samples, context.sampleRate) } : { user_audio_chunk: encode(samples, context.sampleRate) }));
            };
            source.connect(processor); processor.connect(context.destination);
            setPhase(voiceTurn.phase);
          }
          if (data.type === "user_transcript" && !/^[\s.…]*$/.test(data.user_transcription_event?.user_transcript ?? "")) beginTurn();
          if (data.type === "agent_response") {
            voiceTurn.response(data.agent_response_event.agent_response);
            settlePlayback();
          }
          if (data.type === "interruption") {
            playing.forEach(s => { s.onended = null; s.stop(); }); playing.clear(); next = 0;
            voiceTurn.playing = 0; voiceTurn.answered = false; beginTurn();
          }
          if (data.type === "audio") {
            if (!live) beginTurn();
            if (!live) window.clearTimeout(settleTimer);
            const bytes = Uint8Array.from(atob(live ? data.delta : data.audio_event.audio_base_64), c => c.charCodeAt(0));
            const view = new DataView(bytes.buffer);
            const buffer = context.createBuffer(1, Math.floor(bytes.length / 2), 16000);
            const samples = buffer.getChannelData(0);
            for (let i = 0; i < samples.length; i++) samples[i] = view.getInt16(i * 2, true) / 32768;
            const source = context.createBufferSource(); source.buffer = buffer; source.connect(context.destination);
            next = Math.max(next, context.currentTime); source.start(next); next += buffer.duration;
            if (!live) { voiceTurn.queueAudio(); setPhase("speaking"); }
            else if (samples.some(sample => Math.abs(sample) > 0.003)) {
              // Live sends clocked PCM continuously, including silence. Energy
              // affects the visual indicator only; every audio frame is played.
              audibleUntil = next;
              setPhase("speaking"); window.clearTimeout(settleTimer);
              settleTimer = window.setTimeout(() => {
                if (generation.current === turn) setPhase(backendBusy ? "thinking" : "listening");
              }, Math.max(0, next - context.currentTime) * 1000 + 250);
            }
            playing.add(source); source.onended = () => {
              playing.delete(source);
              if (!live) voiceTurn.finishAudio();
              if (generation.current !== turn) return;
              if (!live) settlePlayback();
              else if (providerClosed && !playing.size) stop();
            };
          }
        } catch { setError(m => m.voice.keepFailed); stop(); }
      };
    } catch (e) { if (generation.current === turn) { setError(errorText(e, m => m.voice.unavailable)); stop(); } }
  }
  function closePanel() { if (active) stop(); setPanelOpen(false); }
  const heading = phase === "connecting" ? t.voice.phaseConnecting : phase === "thinking" && !isLive ? t.voice.phaseThinking : phase === "speaking" ? t.voice.phaseSpeaking : t.voice.phaseListening;
  const hint = phase === "connecting" ? t.voice.hintConnecting : isLive ? phase === "thinking" ? t.voice.hintLiveThinking : t.voice.hintLive : phase === "listening" ? t.voice.hintListening : t.voice.hintPaused;
  return <><button type="button" className="channel-action" disabled={disabled || processing} hidden={active} onClick={() => setPanelOpen(true)}><ChannelIcon kind="mic" /><span>{t.voice.action}</span></button>{!panelOpen && error && <p className="channel-error" role="alert">{error(t)}</p>}<ChannelSheet open={panelOpen} onClose={closePanel} title={active ? t.voice.titleActive : t.voice.titleIdle} className={active ? "is-voice-active" : ""}><section className={`voice-control ${active ? "is-active" : ""}`} aria-label={t.voice.sectionLabel}>
    {active ? <div className="voice-stage">
      <div className={`voice-orb is-${phase}`} aria-hidden="true"><span /><span /><span /><span /><span /></div>
      <p className="voice-eyebrow">{t.voice.stageEyebrow}</p>
      <h2 aria-live="polite">{heading}</h2>
      <p>{hint}</p>
      <p className="voice-history-note">{t.voice.historyNote}</p>
    </div> : <><span className="sheet-channel-icon" aria-hidden="true"><ChannelIcon kind="mic" /></span><p>{t.voice.intro}</p>
    <label className="channel-consent"><input type="checkbox" disabled={!voiceInfo} checked={consent} onChange={e => setConsent(e.target.checked)} /><span>{t.voice.consent}</span></label></>}
    <button type="button" disabled={!active && (disabled || !consent || !voiceInfo)} onClick={() => active ? closePanel() : void start()} className="sheet-primary">{active ? t.sheet.backToChat : t.voice.start}</button>
    {error && <p role="alert">{error(t)}</p>}
    {!active && <PrivacyNotice />}
  </section></ChannelSheet></>;
}
