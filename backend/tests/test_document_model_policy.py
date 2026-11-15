from google.genai import types
from pydantic import ValidationError
import pytest

from app.document_settings import DocumentSettings
from app.document_transform.models import VertexGeminiPatchGenerator
from tests.test_aggressive_unslop_models import _generator
from tests.test_config import _settings


@pytest.mark.parametrize(
    "field", ["TRANSFORM_UNSLOP_MODEL", "TRANSFORM_FALLBACK_MODEL", "TRANSFORM_RESTYLE_MODEL"]
)
@pytest.mark.parametrize(
    "model",
    [
        "gemini-3.8-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-pro",
        "unverified-pro-model",
    ],
)
def test_transform_settings_reject_unverified_model_and_thinking_combinations(
    monkeypatch: pytest.MonkeyPatch, field: str, model: str
) -> None:
    with pytest.raises(ValidationError, match="verified Pro-class model with HIGH thinking"):
        DocumentSettings.model_validate({field: model})
    with pytest.raises(ValidationError, match="verified Pro-class model with HIGH thinking"):
        _settings(monkeypatch, **{f"DOCUMENT_{field}": model})


@pytest.mark.parametrize("model", ["gemini-3.1-pro-preview", "gemini-4-argon"])
@pytest.mark.parametrize("repair", [False, True])
def test_allowed_pro_models_keep_high_thinking_in_every_writer_path(
    model: str, *, repair: bool
) -> None:
    harness = _generator(types.GenerateContentResponse())
    generator: VertexGeminiPatchGenerator = harness.generator
    thinking = generator._generation_thinking_config(
        model=model, primary_model=model, repair=repair
    )
    assert thinking is not None
    assert thinking.thinking_level == types.ThinkingLevel.HIGH
