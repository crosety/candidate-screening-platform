"""Pseudonymize known identity and common identifiers, not arbitrary sensitive semantics."""

import re


def pseudonymize(text: str, name: str = "", phone: str = "") -> str:
    # Remove complete email addresses before a name inside one can break the match.
    text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", text)
    for value in sorted({name, phone, *name.split()}, key=len, reverse=True):
        if len(value) >= 3:
            text = re.sub(r"(?<!\w)" + re.escape(value) + r"(?!\w)", "[persona]", text, flags=re.I)
    text = re.sub(r"(?<!\w)(?:\+?\d[\d ()-]{6,}\d)(?!\w)", "[identificador]", text)
    text = re.sub(r"\b(?:\d{8}[A-Za-z]|[XYZxyz]\d{7}[A-Za-z])\b", "[documento]", text)
    return text
