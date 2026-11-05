"""Channel facts are server-owned; candidate text cannot enable a transport."""

import json
import re
import unicodedata

ENGLISH_CALL_REQUESTS = frozenset({"call me", "please call me"})


def _normalized(text: str) -> str:
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )
    return re.sub(r"[¿?¡!.,]", "", normalized).strip()


def call_request_language(text: str) -> str:
    """The language the candidate used to ask for a call: answer in that language."""
    return "en" if _normalized(text) in ENGLISH_CALL_REQUESTS else "es"


def asks_for_call(text: str) -> bool:
    normalized = _normalized(text)
    return normalized in ENGLISH_CALL_REQUESTS or normalized in {
        "llamame",
        "llamadme",
        "llamame por telefono",
        "llamame al movil",
        "puedes llamarme",
        "me puedes llamar",
        "quiero que me llames",
        "llamame por favor",
        "por favor llamame",
    }


def channel_note(
    channel: str,
    *,
    browser_voice: bool = False,
    telephone: bool = False,
    sms_link: bool = False,
    after_call_sms: bool = False,
) -> str:
    facts = {
        "identity": "asistente de inteligencia artificial de la empresa del brief",
        "current_channel": channel,
        "available_channels": {
            "web_text": True,
            "browser_voice": browser_voice,
            "telephone": telephone,
            "sms_link_notifications": sms_link,
            "sms_replies": False,
        },
        "after_call_sms_enabled": after_call_sms,
        "same_application_and_history": True,
        "ending_voice_does_not_complete_application": True,
        "pending_answers_allowed": True,
    }
    return (
        "\n[plataforma: contexto actual de canales]\n"
        + json.dumps(facts, ensure_ascii=False)
        + "\nEl canal actual prevalece sobre referencias antiguas del historial. "
        "SMS solo entrega un enlace personal a esta conversación web; nunca invites a contestar "
        "al SMS ni digas que la entrevista ocurre por SMS. La web permite escribir y, si están "
        "habilitados, hablar por el navegador o solicitar una llamada con «Llámame». "
        "Para una llamada pide confirmar móvil y consentimiento en el formulario; no marques "
        "ni afirmes que has llamado. No prometas canales deshabilitados. Retoma lo ya hablado "
        "sin pedir de nuevo los datos confirmados. Al iniciar un canal de voz preséntate como IA "
        "de la empresa, explica el motivo y enlaza con el contexto anterior. No exijas completar "
        "la entrevista en una llamada: se puede dejar un dato pendiente y continuar en el enlace. "
        "El SMS posterior se envía automáticamente solo si está habilitado y consentido; "
        "no afirmes que ya se ha entregado. No programes recordatorios ni inventes acciones."
    )
