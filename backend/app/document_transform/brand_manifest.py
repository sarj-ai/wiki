from __future__ import annotations

from functools import cache
import hashlib
from importlib.resources import files
import json
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.identifiers import BrandProfileId


CURRENT_BRAND_PROFILE_ID = BrandProfileId("sarj-2026-v4")
BRAND_PROFILE_IDS = frozenset(
    {
        BrandProfileId("sarj-legacy-v1"),
        BrandProfileId("sarj-2026-v2"),
        BrandProfileId("sarj-2026-v3"),
        CURRENT_BRAND_PROFILE_ID,
    }
)
EXPECTED_BRAND_PROFILE_SHA256 = MappingProxyType(
    {
        BrandProfileId("sarj-legacy-v1"): (
            "b2a32a07b41c6e8e4de0fcbcdfe0d156942dbd3e5ea80ab2e62101c523b1c856"
        ),
        BrandProfileId("sarj-2026-v2"): (
            "8824cf836c053056cbe9a3f195944d4720897804264790da750e3c74101654d8"
        ),
        BrandProfileId("sarj-2026-v3"): (
            "4a21dfdc50faa2b1e59d3f6a3397b5a0b6fb248aab4abcfde742e58476321589"
        ),
        CURRENT_BRAND_PROFILE_ID: (
            "bbc319b0152be2a645099c0d88516ebf13b375bdadb5b443ef7037da027dcb22"
        ),
    }
)
HexColor = Annotated[str, StringConstraints(pattern=r"^#[0-9A-F]{6}$")]


class _ManifestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BrandColors(_ManifestModel):
    primary: HexColor
    primary_dark: HexColor
    primary_tint: HexColor
    tint_foreground: HexColor
    ink: HexColor
    muted: HexColor
    paper: HexColor
    subtle: HexColor
    border: HexColor
    chart_ramp: tuple[HexColor, HexColor, HexColor, HexColor, HexColor, HexColor]


class DocumentHeaderBrandTokens(_ManifestModel):
    font_family: Literal["Nunito"]
    size_pt: Literal[9]
    color: HexColor
    bold: Literal[True]
    alignment: Literal["END"]
    line_spacing_percent: Literal[100]
    space_above_pt: Literal[0]
    space_below_pt: Literal[0]
    logo_width_pt: Literal[52]
    logo_height_pt: Literal[28]
    logo_size_tolerance_pt: float = Field(gt=0, le=1)
    max_title_chars: Literal[160]


class DocumentBrandTokens(_ManifestModel):
    font_family: Literal["Nunito"]
    complex_script_font_family: Literal["Arial"] | None = None
    body_size_pt: int = Field(ge=9, le=14)
    h1_size_pt: int = Field(ge=18, le=28)
    h2_size_pt: int = Field(ge=13, le=20)
    h3_size_pt: int = Field(ge=11, le=16)
    caption_size_pt: int = Field(ge=8, le=11)
    line_spacing_percent: int = Field(ge=100, le=140)
    paragraph_space_above_pt: int = Field(ge=0, le=12)
    paragraph_space_below_pt: int = Field(ge=0, le=16)
    horizontal_margin_pt: int = Field(ge=36, le=108)
    vertical_margin_pt: int = Field(ge=36, le=108)
    header: DocumentHeaderBrandTokens | None = None


class SlideBrandTokens(_ManifestModel):
    canvas_width: Literal[1280]
    canvas_height: Literal[720]
    safe_inset: int = Field(ge=48, le=96)
    gaps: tuple[Literal[8], Literal[16], Literal[24], Literal[32], Literal[48], Literal[64]]
    title_size_pt: int = Field(ge=35)
    deck_title_size_pt: int = Field(ge=44)
    body_size_pt: int = Field(ge=16)
    caption_size_pt: int = Field(ge=10)
    max_bullets: int = Field(ge=1, le=5)
    max_cards: int = Field(ge=1, le=3)


class BrandAssets(_ManifestModel):
    logo_uri: Literal["https://wiki.sarj.ai/brand/sarj-logo-light.png"]
    logo_sha256: Literal["c7b088d8caa20b39bcbf885fc142ea291aa5233c011e4859d126b8e45fec8afd"]
    logo_pixel_width: Literal[977] | None = None
    logo_pixel_height: Literal[528] | None = None


class SarjBrandManifest(_ManifestModel):
    schema_version: Literal[1]
    profile_id: BrandProfileId
    assets: BrandAssets
    colors: BrandColors
    document: DocumentBrandTokens
    slides: SlideBrandTokens

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: BrandProfileId) -> BrandProfileId:
        if value not in BRAND_PROFILE_IDS:
            msg = "Unknown Sarj brand profile"
            raise ValueError(msg)
        return value

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json).hexdigest()

    @property
    def canonical_json(self) -> bytes:
        return json.dumps(
            self.model_dump(mode="json", exclude_none=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()


@cache
def load_brand_manifest(profile_id: BrandProfileId) -> SarjBrandManifest:
    if profile_id not in BRAND_PROFILE_IDS:
        msg = "Unknown Sarj brand profile"
        raise ValueError(msg)
    resource = files("app.document_transform.brand_profiles").joinpath(f"{profile_id}.json")
    manifest = SarjBrandManifest.model_validate_json(resource.read_text(encoding="utf-8"))
    if manifest.profile_id != profile_id:
        msg = "Brand profile ID does not match its immutable resource name"
        raise ValueError(msg)
    if manifest.sha256 != EXPECTED_BRAND_PROFILE_SHA256[profile_id]:
        msg = "Brand profile resource changed without a new immutable profile ID"
        raise ValueError(msg)
    return manifest


CURRENT_BRAND_MANIFEST = load_brand_manifest(CURRENT_BRAND_PROFILE_ID)


def trusted_document_logo_description() -> str:
    return (
        f"Sarj logo sha256:{CURRENT_BRAND_MANIFEST.assets.logo_sha256} "
        f"profile:{CURRENT_BRAND_MANIFEST.profile_id}"
    )


# Generated furniture only: append after normalization, never to authored body text.
HEADER_TITLE_SEPARATOR = "\u00a0\u00a0\u00a0\u00a0\t"


def normalize_header_title(title: str, *, max_chars: int) -> str:
    normalized = " ".join(title.split()) or "Untitled"
    return normalized[:max_chars].rstrip()
