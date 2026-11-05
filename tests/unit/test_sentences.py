from screening.domain.diff import diff_sentences, split_sentences


def test_split_sentences() -> None:
    assert split_sentences("Hola. ¿Qué tal? Bien.") == ["Hola.", "¿Qué tal?", "Bien."]


def test_middle_change() -> None:
    assert diff_sentences("Uno. Dos. Tres.", "Uno. Otro. Tres.") == [
        ("=", "Uno."),
        ("-", "Dos."),
        ("+", "Otro."),
        ("=", "Tres."),
    ]
