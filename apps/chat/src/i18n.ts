// Candidate-facing copy in Spanish (source of truth) and English, plus the
// language resolution rules. Free of React and of runtime imports so the node
// tests can load it directly with type stripping.

export const supportedLanguages = ["es", "en"] as const;
export type Language = (typeof supportedLanguages)[number];
export const defaultLanguage: Language = "es";
/** API and socket texts are written in this language. */
export const serverLanguage: Language = "es";
export const languageStorageKey = "orbio.chat.language";

const es = {
  language: "es" as Language,
  locale: "es-ES",
  switcher: { label: "Idioma", names: { es: "Español", en: "English" } as Record<Language, string> },
  app: {
    title: "Tu candidatura",
    openLink: "Abre el enlace que te hemos enviado para empezar.",
  },
  apply: {
    documentTitle: "Solicitar una oferta",
    offerClosed: "Esta oferta ya no admite candidaturas nuevas.",
    offerUnavailable: "No se ha podido abrir esta oferta. Inténtalo de nuevo más tarde.",
    retry: "Reintentar",
    loading: "Abriendo oferta…",
    brandTag: "Selección",
    eyebrow: "Tu próxima oportunidad",
    formTitle: "Empezamos contigo",
    formIntro: "Una conversación breve para conocer tu experiencia y disponibilidad. Puedes responder por escrito o por voz.",
    nameLabel: "Nombre y apellidos",
    phoneLabel: "Teléfono",
    optional: "(opcional)",
    phoneHelp: "Para que el equipo pueda contactar contigo. No se envía ningún mensaje al empezar.",
    consent: "Quiero iniciar la entrevista con el asistente de IA. La voz es opcional.",
    startClosed: "La oferta se ha cerrado. Ya no admite candidaturas.",
    startFailed: "No se ha podido iniciar la entrevista. Puedes volver a intentarlo.",
    starting: "Preparando tu entrevista…",
    start: "Comenzar entrevista →",
    footer: "Tu conversación tiene un enlace privado para poder retomarla.",
  },
  access: {
    invalidLink: "Este enlace no es válido o ha caducado.",
    openFailed: "No se ha podido abrir el chat. Vuelve a intentarlo.",
    eyebrow: "Tu candidatura",
    errorTitle: "No hemos podido abrir la conversación",
    invalidLinkHelp: "Comprueba que has abierto el enlace completo o solicita uno nuevo al equipo de selección.",
    retry: "Volver a intentarlo",
    loadingTitle: "Abriendo tu conversación",
    loadingText: "Estamos recuperando tu proceso de selección.",
  },
  chat: {
    documentTitle: (tenant: string) => `${tenant} · Tu candidatura`,
    statusHandedOff: "Continúa con el equipo",
    statusCompleted: "Conversación completada",
    statusClosed: "Conversación cerrada",
    statusActive: "Conversación en curso",
    contextLabel: "Sobre esta conversación",
    headingEnded: "Gracias por compartir tu experiencia.",
    headingActive: "Hablemos de tu próxima oportunidad.",
    introEnded: "Tu conversación queda disponible para que puedas consultar lo que habéis hablado.",
    introActive: "Cuéntanos tu experiencia y disponibilidad. El asistente te guiará con una pregunta cada vez.",
    processEnded: "Consulta los últimos mensajes para conocer el siguiente paso.",
    processActive: "Puedes responder a tu ritmo.",
    personalLink: "Este enlace es personal.",
    keepLink: "Guárdalo para volver a tu conversación.",
    offerFallback: "Tu proceso de selección",
    presenceHandedOff: "Equipo de selección · Asistente en pausa",
    presenceAssistant: "Asistente virtual",
    online: "En línea",
    connecting: "Conectando…",
    offline: "Sin conexión",
    privateLink: "Enlace personal",
    transcriptLabel: "Conversación",
    introduction: (tenant: string) => `Conversación con el asistente virtual de ${tenant}`,
    openingFailedTitle: "No hemos podido iniciar la entrevista",
    openingTitle: "El asistente está preparando tu entrevista",
    openingFailedText: "Vuelve a conectar para intentarlo de nuevo.",
    openingText: "Se presentará y te hará la primera pregunta. Después podrás responder por escrito o por voz.",
    authorYou: "Tú",
    authorTeam: "Equipo de selección",
    authorAssistant: "Asistente",
    sending: "Enviando…",
    typing: "El asistente está escribiendo…",
    newMessages: "Ver mensajes recientes",
    reconnect: "Volver a conectar",
    handoffTitle: "Atención del equipo de selección",
    handoffText: "El asistente está en pausa. Puedes dejar aquí tu mensaje para que el equipo continúe la conversación.",
    endedHandedOff: "Una persona del equipo seguirá contigo.",
    endedHistory: "Puedes consultar el historial de esta conversación.",
    messageLabel: "Tu mensaje",
    placeholderWaiting: "Espera la respuesta del asistente…",
    placeholder: "Escribe tu mensaje…",
    send: "Enviar mensaje",
  },
  connection: {
    renewFailed: "No se ha podido renovar el acceso. Recarga la página.",
    unreadable: "No se ha podido leer la respuesta del servidor.",
    invalidLink: "Este enlace no es válido.",
    lost: "Se ha perdido la conexión. Recarga la página para volver a intentarlo.",
    // Used when the server sends a code without text; Spanish texts come from the server.
    serverErrors: {
      invalid_message: "Escribe entre 1 y 2.000 caracteres.",
      voice_active: "Sal de voz antes de escribir.",
      session_closed: "La conversación ya ha terminado.",
      not_allowed: "No se puede enviar este mensaje.",
      turn_failed: "No se ha podido responder. Vuelve a intentarlo.",
      default: "No se ha podido enviar el mensaje. Vuelve a intentarlo.",
    } as Record<string, string>,
  },
  sheet: {
    close: "Cerrar panel",
    backToChat: "Volver al chat",
    mobileLabel: "Tu móvil",
    requesting: "Solicitando…",
  },
  privacy: {
    title: "Privacidad y uso de IA",
    assistant: "Conversas con un asistente de inteligencia artificial. Tus respuestas y el historial quedan vinculados a esta candidatura para que puedas continuarla y el equipo de selección pueda revisarla.",
    voice: "La voz es opcional. Solo se activa el micrófono si lo autorizas y siempre puedes continuar por escrito. Si solicitas una llamada, utilizaremos el número que indiques para contactarte.",
    share: "Comparte únicamente información necesaria para la entrevista. No envíes documentos de identidad, datos médicos ni información de otras personas.",
    processingSummary: "Detalles del procesamiento",
    processing: "El texto se procesa mediante OpenAI. Para la voz se utiliza ElevenLabs y las llamadas telefónicas se realizan a través de Twilio. Estos servicios pueden procesar información fuera de la Unión Europea. El historial se conserva junto a tu candidatura.",
  },
  voice: {
    action: "Hablar aquí",
    titleActive: "Conversación por voz",
    titleIdle: "Hablemos por aquí",
    sectionLabel: "Voz",
    stageEyebrow: "Conversación por voz",
    phaseConnecting: "Conectando…",
    phaseThinking: "Preparando respuesta",
    phaseSpeaking: "El asistente está hablando",
    phaseListening: "Te escucho",
    hintConnecting: "Preparando tu conversación.",
    hintLiveThinking: "Estoy revisando lo que me has contado. Puedes seguir hablando.",
    hintLive: "Puedes hablar e interrumpir en cualquier momento.",
    hintListening: "Puedes hablar ahora.",
    hintPaused: "Te escucho en cuanto termine la respuesta.",
    historyNote: "Cuando termines, puedes volver a escribir. Lo que hablemos queda en esta conversación.",
    intro: "Habla con el asistente de IA desde este navegador. No recibirás una llamada.",
    consent: "Quiero activar el micrófono y continuar por voz.",
    start: "Empezar a hablar",
    configFailed: "No hemos podido preparar la voz. Cierra este panel y vuelve a intentarlo.",
    unavailableNow: "La voz no está disponible ahora.",
    stalled: "La respuesta de voz está tardando demasiado. La voz se ha desconectado; puedes continuar por escrito.",
    connectFailed: "No se pudo conectar la voz.",
    liveClosed: "La conexión de voz se ha cerrado. Puedes continuar por escrito.",
    closedEarly: "La voz se ha cerrado antes de completar la respuesta. Puedes continuar por escrito.",
    liveFailed: "No se pudo mantener la voz.",
    interrupted: "La voz se ha interrumpido. Puedes continuar por escrito.",
    tooSlow: "La conexión de audio es demasiado lenta.",
    keepFailed: "No se ha podido mantener la conversación por voz. Puedes volver a intentarlo o seguir escribiendo.",
    unavailable: "Voz no disponible.",
  },
  voiceErrors: {
    startUnavailable: "Voz no disponible. Puedes seguir por escrito.",
    unavailableNow: "La conversación por voz no está disponible ahora. Puedes continuar por escrito.",
    dailyLimit: "La conversación por voz no está disponible ahora. Puedes continuar por escrito.",
    concurrency: "Hay otra llamada de voz en curso. Ciérrala o espera a que termine antes de volver a intentarlo.",
    callback: "El servicio de voz no ha podido obtener una respuesta del asistente. Puedes continuar por escrito mientras se revisa la conexión.",
    closed: "La conexión de voz se ha cerrado. Comprueba tu conexión o continúa por escrito.",
  },
  telephone: {
    retry: "Reintentar",
    action: "Llámame",
    viewCall: "Ver llamada",
    titleActive: "Tu llamada",
    titleIdle: "Te llamamos",
    states: {
      queued: "Estamos preparando tu llamada.",
      dialing: "Te estamos llamando…",
      ringing: "Estamos intentando contactar contigo.",
      "in-progress": "Estás hablando por teléfono.",
      unknown: "No hemos podido confirmar la llamada. Comprueba tu móvil antes de intentarlo de nuevo.",
      completed: "Llamada finalizada. Puedes continuar aquí.",
      busy: "Tu móvil estaba ocupado. Puedes solicitar otra llamada cuando te venga bien.",
      "no-answer": "No hemos podido contactar contigo. Puedes volver a solicitar la llamada.",
      failed: "No hemos podido completar la llamada. Puedes seguir por aquí.",
      canceled: "La llamada se ha cancelado. Puedes continuar aquí.",
    } as Record<string, string>,
    checking: "Consultando tu llamada…",
    statusFailed: "No hemos podido comprobar la llamada. Vuelve a intentarlo en un momento.",
    checkingAvailability: "Comprobando la disponibilidad…",
    unavailable: "Las llamadas no están disponibles ahora. Puedes continuar por escrito.",
    activeNote: "Tu conversación sigue guardada aquí. Cuando termines, puedes retomarla por escrito o por voz.",
    requestAnother: "Solicitar otra llamada",
    intro: "Continúa esta entrevista por teléfono con el asistente de IA. Te llamamos al número que indiques.",
    consent: "Quiero recibir la llamada en este número.",
    callNow: "Llámame ahora",
    footnote: "Lo que ya hemos hablado se conserva. Puedes seguir después en este chat.",
    requestUnavailable: "No se puede solicitar la llamada ahora. Puedes continuar por aquí.",
    invalidMobile: "Indica un móvil español válido con prefijo +34.",
    callHours: "Las llamadas están disponibles de 08:00 a 22:00 en tu horario local. Puedes continuar por escrito.",
    requestUnconfirmed: "No hemos podido confirmar tu solicitud. Comprueba si recibes la llamada antes de volver a intentarlo.",
    requestFailed: "No hemos podido confirmar tu solicitud. Comprueba tu móvil antes de volver a intentarlo.",
  },
  sms: {
    action: "Mi enlace por SMS",
    checking: "Comprobando los SMS…",
    statusFailed: "No hemos podido comprobar los SMS. Vuelve a intentarlo antes de solicitar otro envío.",
    retry: "Reintentar SMS",
    unavailable: "Los SMS no están disponibles ahora. Puedes continuar en este chat.",
    title: "Tu enlace por SMS",
    intro: "Guarda el enlace para volver cuando quieras. Después de una llamada también puedes continuar en esta conversación.",
    states: {
      queued: "Preparando tu enlace",
      sending: "Enviando",
      accepted: "Preparando el envío",
      sent: "Enviado",
      delivered: "Entregado",
      failed: "No se ha podido enviar",
      undelivered: "No se ha podido entregar",
      unknown: "No hemos podido confirmar el envío. Comprueba tus mensajes.",
      canceled: "Cancelado",
    } as Record<string, string>,
    statePending: "Pendiente",
    purposeAfterCall: "Después de la llamada",
    purposeLink: "Enlace personal",
    alertsOff: "Avisos desactivados",
    consent: "Quiero recibir el enlace por SMS y un aviso al terminar la llamada.",
    send: "Enviarme el enlace",
    revoke: "No recibir más avisos",
    confirmFailed: "No se pudo confirmar el aviso SMS.",
    requestFailed: "No se pudo confirmar la solicitud.",
  },
  interview: {
    title: "Tu entrevista",
    loading: "Comprobando tu entrevista…",
    loadFailed: "No hemos podido comprobar tu entrevista. Los datos anteriores pueden no estar actualizados.",
    retry: "Reintentar entrevista",
    states: { booked: "Reservada", rescheduled: "Cambiada", cancelled: "Cancelada" } as Record<string, string>,
    duration: (minutes: number) => `${minutes} minutos con la persona encargada del local`,
    addToCalendar: "Añadir al calendario",
    removeFromCalendar: "Quitar de mi calendario",
    manage: "Cambiar o cancelar",
    managing: "Abriendo…",
    manageHint: "Escribe aquí si prefieres otra fecha o si quieres cancelarla.",
    manageFailed: "No hemos podido abrir la conversación. Vuelve a intentarlo.",
    downloadFailed: "No hemos podido preparar el archivo del calendario. Vuelve a intentarlo.",
  },
};

/** Shape shared by every dictionary; a missing or extra key fails type checking. */
export type Messages = typeof es;
/** Text resolved against the current dictionary, so stored notices follow a language change. */
export type Localized = (messages: Messages) => string;

const en: Messages = {
  language: "en",
  locale: "en-GB",
  switcher: { label: "Language", names: { es: "Español", en: "English" } },
  app: {
    title: "Your application",
    openLink: "Open the link we sent you to get started.",
  },
  apply: {
    documentTitle: "Apply for a job",
    offerClosed: "This job is no longer accepting applications.",
    offerUnavailable: "We couldn't open this job. Please try again later.",
    retry: "Try again",
    loading: "Opening the job…",
    brandTag: "Careers",
    eyebrow: "Your next opportunity",
    formTitle: "Let's get started",
    formIntro: "A short conversation to learn about your experience and availability. You can reply in writing or by voice.",
    nameLabel: "Full name",
    phoneLabel: "Phone",
    optional: "(optional)",
    phoneHelp: "So the team can get in touch. We won't send you anything when you start.",
    consent: "I want to start the interview with the AI assistant. Voice is optional.",
    startClosed: "This job has closed and is no longer accepting applications.",
    startFailed: "We couldn't start the interview. Please try again.",
    starting: "Preparing your interview…",
    start: "Start interview →",
    footer: "Your conversation has a private link so you can pick it up later.",
  },
  access: {
    invalidLink: "This link isn't valid or has expired.",
    openFailed: "We couldn't open the chat. Please try again.",
    eyebrow: "Your application",
    errorTitle: "We couldn't open your conversation",
    invalidLinkHelp: "Make sure you opened the full link, or ask the hiring team for a new one.",
    retry: "Try again",
    loadingTitle: "Opening your conversation",
    loadingText: "We're loading your application.",
  },
  chat: {
    documentTitle: (tenant: string) => `${tenant} · Your application`,
    statusHandedOff: "Continuing with the team",
    statusCompleted: "Conversation complete",
    statusClosed: "Conversation closed",
    statusActive: "Conversation in progress",
    contextLabel: "About this conversation",
    headingEnded: "Thanks for sharing your experience.",
    headingActive: "Let's talk about your next opportunity.",
    introEnded: "Your conversation stays available so you can look back at what you talked about.",
    introActive: "Tell us about your experience and availability. The assistant will guide you one question at a time.",
    processEnded: "Check the latest messages to see what happens next.",
    processActive: "Reply at your own pace.",
    personalLink: "This link is just for you.",
    keepLink: "Save it to come back to your conversation.",
    offerFallback: "Your application",
    presenceHandedOff: "Hiring team · Assistant paused",
    presenceAssistant: "Virtual assistant",
    online: "Online",
    connecting: "Connecting…",
    offline: "Offline",
    privateLink: "Personal link",
    transcriptLabel: "Conversation",
    introduction: (tenant: string) => `Conversation with ${tenant}'s virtual assistant`,
    openingFailedTitle: "We couldn't start the interview",
    openingTitle: "The assistant is getting your interview ready",
    openingFailedText: "Reconnect to try again.",
    openingText: "It will introduce itself and ask the first question. Then you can reply in writing or by voice.",
    authorYou: "You",
    authorTeam: "Hiring team",
    authorAssistant: "Assistant",
    sending: "Sending…",
    typing: "The assistant is typing…",
    newMessages: "See new messages",
    reconnect: "Reconnect",
    handoffTitle: "You're now with the hiring team",
    handoffText: "The assistant is paused. Leave your message here and the team will pick up the conversation.",
    endedHandedOff: "Someone from the team will follow up with you.",
    endedHistory: "You can still look back at this conversation.",
    messageLabel: "Your message",
    placeholderWaiting: "Waiting for the assistant's reply…",
    placeholder: "Type your message…",
    send: "Send message",
  },
  connection: {
    renewFailed: "We couldn't renew your access. Please reload the page.",
    unreadable: "We couldn't read the latest update. Please reload the page.",
    invalidLink: "This link isn't valid.",
    lost: "The connection was lost. Reload the page to try again.",
    serverErrors: {
      invalid_message: "Messages must be between 1 and 2,000 characters.",
      voice_active: "Leave voice mode before typing.",
      session_closed: "This conversation has ended.",
      not_allowed: "This message can't be sent.",
      turn_failed: "We couldn't reply. Please try again.",
      default: "We couldn't send your message. Please try again.",
    },
  },
  sheet: {
    close: "Close",
    backToChat: "Back to chat",
    mobileLabel: "Your mobile number",
    requesting: "Requesting…",
  },
  privacy: {
    title: "Privacy and AI use",
    assistant: "You're talking to an artificial intelligence assistant. Your answers and the conversation history are linked to this application so you can pick it up again and the hiring team can review it.",
    voice: "Voice is optional. The microphone only turns on if you allow it, and you can always continue in writing. If you ask for a call, we'll use the number you give us to contact you.",
    share: "Only share what's needed for the interview. Please don't send ID documents, medical information or details about other people.",
    processingSummary: "Processing details",
    processing: "Text is processed by OpenAI. Voice uses ElevenLabs, and phone calls are made through Twilio. These services may process information outside the European Union. The history is kept with your application.",
  },
  voice: {
    action: "Talk here",
    titleActive: "Voice conversation",
    titleIdle: "Let's talk here",
    sectionLabel: "Voice",
    stageEyebrow: "Voice conversation",
    phaseConnecting: "Connecting…",
    phaseThinking: "Preparing a reply",
    phaseSpeaking: "The assistant is speaking",
    phaseListening: "I'm listening",
    hintConnecting: "Getting your conversation ready.",
    hintLiveThinking: "I'm going over what you've told me. Feel free to keep talking.",
    hintLive: "You can speak or interrupt at any time.",
    hintListening: "Go ahead and speak.",
    hintPaused: "I'll listen again as soon as I finish replying.",
    historyNote: "When you're done, you can go back to typing. Everything we say stays in this conversation.",
    intro: "Talk to the AI assistant right here in your browser. You won't get a phone call.",
    consent: "I want to turn on the microphone and continue by voice.",
    start: "Start talking",
    configFailed: "We couldn't set up voice. Close this panel and try again.",
    unavailableNow: "Voice isn't available right now.",
    stalled: "The voice reply is taking too long, so voice has been disconnected. You can keep going in writing.",
    connectFailed: "Voice couldn't connect.",
    liveClosed: "The voice connection closed. You can keep going in writing.",
    closedEarly: "Voice closed before the reply was finished. You can keep going in writing.",
    liveFailed: "Voice couldn't continue.",
    interrupted: "Voice was interrupted. You can keep going in writing.",
    tooSlow: "Your audio connection is too slow.",
    keepFailed: "We couldn't keep the voice conversation going. You can try again or keep typing.",
    unavailable: "Voice isn't available.",
  },
  voiceErrors: {
    startUnavailable: "Voice isn't available. You can keep going in writing.",
    unavailableNow: "Voice conversations aren't available right now. You can keep going in writing.",
    dailyLimit: "Voice conversations aren't available right now. You can keep going in writing.",
    concurrency: "Another voice call is already in progress. Close it or wait for it to end before trying again.",
    callback: "The voice service couldn't get a reply from the assistant. You can keep going in writing while we check the connection.",
    closed: "The voice connection closed. Check your connection or keep going in writing.",
  },
  telephone: {
    retry: "Retry",
    action: "Call me",
    viewCall: "View call",
    titleActive: "Your call",
    titleIdle: "We'll call you",
    states: {
      queued: "We're setting up your call.",
      dialing: "Calling you now…",
      ringing: "Trying to reach you.",
      "in-progress": "You're on the call.",
      unknown: "We couldn't confirm the call. Check your phone before trying again.",
      completed: "Call ended. You can carry on here.",
      busy: "Your line was busy. You can ask for another call whenever it suits you.",
      "no-answer": "We couldn't reach you. You can ask for the call again.",
      failed: "We couldn't complete the call. You can carry on here.",
      canceled: "The call was canceled. You can carry on here.",
    },
    checking: "Checking on your call…",
    statusFailed: "We couldn't check on your call. Try again in a moment.",
    checkingAvailability: "Checking availability…",
    unavailable: "Calls aren't available right now. You can keep going in writing.",
    activeNote: "Your conversation is saved here. When you're done, you can pick it up again in writing or by voice.",
    requestAnother: "Ask for another call",
    intro: "Continue this interview by phone with the AI assistant. We'll call the number you enter.",
    consent: "I want to receive the call on this number.",
    callNow: "Call me now",
    footnote: "Everything we've covered so far is kept. You can carry on in this chat afterwards.",
    requestUnavailable: "You can't ask for a call right now. You can carry on here.",
    invalidMobile: "Enter a valid Spanish mobile number with the +34 prefix.",
    callHours: "Calls are available from 08:00 to 22:00 in your local time. You can continue in writing.",
    requestUnconfirmed: "We couldn't confirm your request. Check whether the call comes through before trying again.",
    requestFailed: "We couldn't confirm your request. Check your phone before trying again.",
  },
  sms: {
    action: "Text me my link",
    checking: "Checking your SMS messages…",
    statusFailed: "We couldn't check your SMS messages. Try again before requesting another message.",
    retry: "Retry SMS",
    unavailable: "SMS isn't available right now. You can keep going in this chat.",
    title: "Your link by text",
    intro: "Keep the link so you can come back whenever you like. After a call, you can also carry on in this conversation.",
    states: {
      queued: "Preparing your link",
      sending: "Sending",
      accepted: "Getting ready to send",
      sent: "Sent",
      delivered: "Delivered",
      failed: "Couldn't be sent",
      undelivered: "Couldn't be delivered",
      unknown: "We couldn't confirm it was sent. Check your messages.",
      canceled: "Canceled",
    },
    statePending: "Pending",
    purposeAfterCall: "After the call",
    purposeLink: "Personal link",
    alertsOff: "Notifications off",
    consent: "I want to get the link by text, plus a message when the call ends.",
    send: "Send me the link",
    revoke: "Stop text messages",
    confirmFailed: "We couldn't confirm the text message.",
    requestFailed: "We couldn't confirm your request.",
  },
  interview: {
    title: "Your interview",
    loading: "Checking your interview…",
    loadFailed: "We couldn't check your interview. Any previous details may be out of date.",
    retry: "Retry interview",
    states: { booked: "Booked", rescheduled: "Changed", cancelled: "Cancelled" },
    duration: (minutes: number) => `${minutes} minutes with the restaurant manager`,
    addToCalendar: "Add to calendar",
    removeFromCalendar: "Remove from my calendar",
    manage: "Change or cancel",
    managing: "Opening…",
    manageHint: "Write here if you'd prefer another date or want to cancel it.",
    manageFailed: "We couldn't reopen the conversation. Please try again.",
    downloadFailed: "We couldn't prepare the calendar file. Please try again.",
  },
};

export const dictionaries: Record<Language, Messages> = { es, en };

export function isLanguage(value: unknown): value is Language {
  return typeof value === "string" && (supportedLanguages as readonly string[]).includes(value);
}

/** Map a BCP 47 tag such as `en-GB`, `es-419` or a bare `EN` to a supported language. */
export function matchLanguage(tag: unknown): Language | null {
  if (typeof tag !== "string") return null;
  const primary = tag.trim().toLowerCase().split(/[-_]/)[0];
  return isLanguage(primary) ? primary : null;
}

/** `?lang=es|en`; anything else is ignored. */
export function languageFromSearch(search: string): Language | null {
  return matchLanguage(new URLSearchParams(search).get("lang"));
}

/**
 * Resolution order: explicit `?lang=`, then the choice remembered in this
 * browser (switch or an earlier `?lang=`), then the language the server
 * classified for the session, then the first browser language we support,
 * and Spanish otherwise.
 */
export function resolveLanguage({ search = "", stored = null, session = "", preferred = [] }: {
  search?: string; stored?: string | null; session?: string | null; preferred?: readonly string[];
}): Language {
  const explicit = languageFromSearch(search) ?? matchLanguage(stored);
  if (explicit) return explicit;
  const classified = matchLanguage(session);
  if (classified) return classified;
  for (const tag of preferred) {
    const match = matchLanguage(tag);
    if (match) return match;
  }
  return defaultLanguage;
}

type LanguageStorage = Pick<Storage, "getItem" | "setItem">;

// Even reading `window.localStorage` can throw (blocked site data, sandboxed
// frames), so the accessor itself runs inside the callers' try blocks.
function browserStorage(): LanguageStorage {
  return window.localStorage;
}

export function readStoredLanguage(storage: () => LanguageStorage = browserStorage): Language | null {
  try {
    return matchLanguage(storage().getItem(languageStorageKey));
  } catch {
    return null;
  }
}

export function storeLanguage(language: Language, storage: () => LanguageStorage = browserStorage): void {
  try {
    storage().setItem(languageStorageKey, language);
  } catch {
    // Without storage the choice still applies to this visit.
  }
}

export function browserLanguages(): readonly string[] {
  try {
    if (navigator.languages?.length) return navigator.languages;
    return navigator.language ? [navigator.language] : [];
  } catch {
    return [];
  }
}

/** API and socket texts are Spanish: show them verbatim only in a Spanish interface. */
export function serverText(text: unknown, fallback: Localized): Localized {
  return messages => messages.language === serverLanguage && typeof text === "string" && text.trim() ? text : fallback(messages);
}

/** An error whose user-facing text follows the current language. */
export class LocalizedError extends Error {
  readonly text: Localized;
  readonly code: string;
  constructor(text: Localized, code = "") {
    super(code || "localized_error");
    this.text = text;
    this.code = code;
  }
}

export function errorText(cause: unknown, fallback: Localized): Localized {
  return cause instanceof LocalizedError ? cause.text : fallback;
}
