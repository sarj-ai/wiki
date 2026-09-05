from __future__ import annotations

import json

from pydantic import ValidationError
import pytest

from app.document_transform.brand import SarjBrandProfile
from app.document_transform.brand_manifest import (
    CURRENT_BRAND_MANIFEST,
    EXPECTED_BRAND_PROFILE_SHA256,
    load_brand_manifest,
)


def test_brand_manifests_are_immutable_canonical_and_distinct() -> None:
    legacy = load_brand_manifest("sarj-legacy-v1")
    previous = load_brand_manifest("sarj-2026-v2")
    prior_current = load_brand_manifest("sarj-2026-v3")
    current = load_brand_manifest("sarj-2026-v4")

    assert current.sha256 == load_brand_manifest("sarj-2026-v4").sha256
    assert current.sha256 != legacy.sha256
    assert current.sha256 != prior_current.sha256
    assert previous.sha256 == ("8824cf836c053056cbe9a3f195944d4720897804264790da750e3c74101654d8")
    assert prior_current.sha256 == (
        "4a21dfdc50faa2b1e59d3f6a3397b5a0b6fb248aab4abcfde742e58476321589"
    )
    assert current.sha256 == EXPECTED_BRAND_PROFILE_SHA256[current.profile_id]
    assert json.loads(current.canonical_json)["profile_id"] == "sarj-2026-v4"
    assert current.assets.logo_sha256 == (
        "c7b088d8caa20b39bcbf885fc142ea291aa5233c011e4859d126b8e45fec8afd"
    )
    assert current.colors.primary == "#392868"
    assert current.document.font_family == "Nunito"
    assert current.document.complex_script_font_family == "Arial"
    assert prior_current.document.complex_script_font_family is None
    assert previous.document.complex_script_font_family is None
    assert legacy.document.complex_script_font_family is None
    assert current.document.header is not None
    assert current.document.header.logo_width_pt == 52
    assert current.slides.canvas_width / current.slides.canvas_height == 16 / 9


def test_document_profile_is_derived_from_current_manifest() -> None:
    profile = SarjBrandProfile()

    assert profile.version == CURRENT_BRAND_MANIFEST.profile_id
    assert profile.sha256 == CURRENT_BRAND_MANIFEST.sha256
    assert profile.primary_color == CURRENT_BRAND_MANIFEST.colors.primary
    assert profile.font_family == CURRENT_BRAND_MANIFEST.document.font_family


def test_brand_manifest_rejects_unapproved_palette_value() -> None:
    changed = CURRENT_BRAND_MANIFEST.model_dump(mode="json")
    changed["colors"]["primary"] = "purple"

    with pytest.raises(ValidationError):
        type(CURRENT_BRAND_MANIFEST).model_validate(changed)


def test_runtime_brand_profile_rejects_token_overrides() -> None:
    with pytest.raises(ValueError, match="pinned manifest"):
        SarjBrandProfile(primary_color="#000000")
