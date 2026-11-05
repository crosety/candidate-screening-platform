"""Per-turn observations that adapt tone and language; never hiring criteria."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Mood = Literal["neutral", "positive", "confused", "frustrated", "anxious"]

# What the platform tells the agent for each mood. Neutral and positive change nothing.
TONE: dict[str, tuple[str, str]] = {
    "frustrated": (
        "empathize",
        # Never invite a handoff proactively (H14.7); a person stays available on request.
        "Reconoce la molestia en una frase, acorta los mensajes, di cuántos datos faltan y "
        "sigue sin repetir preguntas ya respondidas.",
    ),
    "confused": (
        "simplify",
        "Reformula lo último con palabras sencillas y un ejemplo, y haz una sola pregunta cerrada.",
    ),
    "anxious": (
        "reassure",
        "Tranquiliza: explica que puede dejar un dato pendiente y seguir después, sin presión.",
    ),
}


class TurnSignal(BaseModel):
    """Classifier output for one candidate message."""

    model_config = ConfigDict(extra="forbid")
    language: str = Field(default="", pattern=r"^(?:[a-z]{2})?$")
    code_switching: bool = False
    mood: Mood = "neutral"
    quote: str = Field(default="", max_length=300)

    def grounded(self, text: str) -> "TurnSignal":
        """A mood without a literal quote from the message is not an observation."""
        quote = self.quote.strip()
        if quote and quote not in text:
            # Keep the candidate's literal spelling when only the case differs.
            start = text.casefold().find(quote.casefold())
            same_length = len(text.casefold()) == len(text)
            quote = text[start : start + len(quote)] if start >= 0 and same_length else ""
        mood = self.mood if quote or self.mood == "neutral" else "neutral"
        return self.model_copy(update={"quote": quote if mood != "neutral" else "", "mood": mood})

    @property
    def tone(self) -> str:
        return TONE[self.mood][0] if self.mood in TONE else ""

    def stored(self) -> dict[str, str | bool]:
        return {
            "mood": self.mood,
            "quote": self.quote,
            "tone": self.tone,
            "code_switching": self.code_switching,
        }

    def note(self) -> str:
        """Platform note for this turn; the brief still decides which languages are allowed."""
        if not self.language and self.mood == "neutral":
            return ""
        lines = ["[plataforma: observación del último mensaje; no es criterio de selección]"]
        if self.language:
            mixed = " (mezcla idiomas)" if self.code_switching else ""
            lines.append(
                f"Idioma predominante: {self.language}{mixed}. Aplica la regla de idioma del "
                "brief a esta respuesta; si la permite, contesta en ese idioma."
            )
        if self.mood in TONE:
            lines.append(f"Ánimo observado: {self.mood} («{self.quote}»). {TONE[self.mood][1]}")
        return "\n".join(lines)
