from pathlib import Path

from adapters.personas import FilePersonaSource
from screening.domain.languages import language_variants


def test_variants_preserve_facts_verdict_and_adversarial_checks() -> None:
    base = FilePersonaSource(Path(__file__).resolve().parents[2] / "seeds").list_personas(
        "grupo_sazon"
    )
    variants = language_variants(base)
    assert len(variants) == len(base) * 2
    for original, english, mixed in zip(base, variants[::2], variants[1::2], strict=True):
        assert original.facts == english.facts == mixed.facts
        assert original.expected_verdict == english.expected_verdict == mixed.expected_verdict
        assert original.must_not == english.must_not == mixed.must_not
        assert original.must == english.must == mixed.must
