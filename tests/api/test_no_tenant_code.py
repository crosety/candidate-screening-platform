from pathlib import Path


def test_no_tenant_specific_code() -> None:
    root = Path(__file__).resolve().parents[2]
    files = list((root / "src").rglob("*.py"))
    for directory in (root / "apps").glob("*/src"):
        files.extend(directory.rglob("*.ts"))
        files.extend(directory.rglob("*.tsx"))
    # Client names and client field keys belong to profiles and seeds, never to platform code.
    forbidden = (
        "sazon",
        "sazón",
        "logistica",
        "logística",
        "grupo_sazon",
        "local_asignado",
        "nombre_completo",
        "incorporacion",
        "repartidor",
    )
    violations = [
        (str(path.relative_to(root)), term)
        for path in files
        for term in forbidden
        if term in path.read_text().lower()
    ]
    assert not violations, violations
