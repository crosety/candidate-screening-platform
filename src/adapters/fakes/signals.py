import re
import unicodedata

from screening.domain.profile import ResultField
from screening.domain.run_context import RunContext
from screening.domain.signals import Mood, TurnSignal

_SPANISH = {
    "el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "es", "no", "sí", "si",
    "tengo", "soy", "vivo", "puedo", "quiero", "por", "para", "con", "mi", "me", "pero",
    "hola", "gracias", "vale", "trabajo", "carnet", "cuando", "dónde", "donde", "qué", "estoy",
}  # fmt: skip
_ENGLISH = {
    "the", "a", "an", "and", "of", "to", "in", "is", "i", "you", "yes", "my", "have", "live",
    "can", "want", "work", "with", "but", "hello", "hi", "thanks", "please", "license", "when",
    "where", "what", "i'm", "am", "do", "don't", "it", "this", "that", "would", "could",
}  # fmt: skip
_MOODS: tuple[tuple[Mood, tuple[str, ...]], ...] = (
    (
        "frustrated",
        (
            "ya te lo he dicho", "ya lo he dicho", "otra vez", "estoy harto", "estoy harta",
            "qué pesad", "que pesad", "me estás mareando", "no me escuchas", "this is annoying",
            "i already told you", "stop asking", "ridiculous", "ridículo",
        ),
    ),
    (
        "confused",
        (
            "no entiendo", "no lo entiendo", "a qué te refieres", "qué quieres decir",
            "no sé qué", "what do you mean", "i don't understand", "confused", "lío",
        ),
    ),
    (
        "anxious",
        ("me preocupa", "tengo miedo", "estoy nervios", "i'm worried", "i am worried", "nervous"),
    ),
    ("positive", ("genial", "perfecto", "me encanta", "great", "awesome", "perfect", "gracias!")),
)  # fmt: skip


def _plain(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )


class KeywordSignalClassifier:
    """Deterministic double for tests and the fake runtime; no model involved."""

    async def classify(self, ctx: RunContext, text: str) -> TurnSignal:
        words = re.findall(r"[a-záéíóúñü']+", text.casefold())
        spanish = sum(w in _SPANISH for w in words)
        english = sum(w in _ENGLISH for w in words)
        language = ""
        if spanish + english >= 2 or (spanish + english == 1 and len(words) <= 3):
            language = "es" if spanish >= english else "en"
        mixed = spanish >= 2 and english >= 2
        lowered = text.casefold()
        for mood, phrases in _MOODS:
            for phrase in phrases:
                start = lowered.find(phrase)
                if start >= 0:
                    quote = text[start : start + len(phrase)]
                    return TurnSignal(
                        language=language, code_switching=mixed, mood=mood, quote=quote
                    ).grounded(text)
        return TurnSignal(language=language, code_switching=mixed)

    async def pending_stage(self, ctx: RunContext, question: str, fields: list[ResultField]) -> str:
        plain = _plain(question)
        if any(word in plain for word in ("hueco", "cita", "entrevista", "slot", "interview")):
            return "interview"
        if any(word in plain for word in ("resumen", "confirmas", "summary")):
            return "summary"
        for field in fields:
            terms = [field.key.replace("_", " "), *_plain(field.label).split()]
            if any(len(term) > 3 and term in plain for term in terms):
                return field.key
        return ""
