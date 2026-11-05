from pathlib import Path

import pytest

from screening.domain.profile import ProfileContent


@pytest.fixture
def profile() -> ProfileContent:
    path = Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/profile.json"
    return ProfileContent.model_validate_json(path.read_text(encoding="utf-8"))
