from screening.domain.evals import Persona


def language_variants(personas: list[Persona]) -> list[Persona]:
    """Only style changes. Facts, expected verdicts and adversarial checks stay intact."""
    return [
        persona.model_copy(
            update={
                "key": f"{persona.key}_{suffix}",
                "language": suffix,
                "style": instruction + " " + persona.style,
            }
        )
        for persona in personas
        for suffix, instruction in (
            ("en", "Contesta en inglés durante toda la conversación."),
            ("mixed", "Alterna español e inglés con naturalidad, sin cambiar ningún dato."),
        )
    ]
