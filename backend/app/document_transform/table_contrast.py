from pydantic import BaseModel, ConfigDict, Field, JsonValue


# Tables use 10 pt text, so use the normal-text threshold without rounding.
# https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html
_MINIMUM_CONTRAST = 4.5
_SRGB_BREAKPOINT = 0.04045


class _ColorModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _RgbColor(_ColorModel):
    red: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    green: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    blue: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)

    @property
    def luminance(self) -> float:
        return sum(
            weight * _linear_component(value)
            for weight, value in zip(
                (0.2126, 0.7152, 0.0722), (self.red, self.green, self.blue), strict=True
            )
        )


class _Color(_ColorModel):
    rgb: _RgbColor = Field(default_factory=_RgbColor, alias="rgbColor")


class _OptionalColor(_ColorModel):
    color: _Color | None = None


class _TextColors(_ColorModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)

    foreground: _OptionalColor | None = Field(default=None, alias="foregroundColor")
    background: _OptionalColor | None = Field(default=None, alias="backgroundColor")


def table_text_has_readable_contrast(style: dict[str, JsonValue], background: JsonValue) -> bool:
    colors = _TextColors.model_validate(style)
    cell = _OptionalColor.model_validate(background).color
    if cell is None:
        return False
    if colors.foreground is None:
        if "foreground" in colors.model_fields_set:
            return False
        # The caller has resolved named styles; an omitted foreground is black.
        foreground = _RgbColor()
    elif colors.foreground.color is None:
        return False  # Explicit transparent text cannot prove readability.
    else:
        foreground = colors.foreground.color.rgb
    if colors.background is None:
        if "background" in colors.model_fields_set:
            return False
        backdrop = cell.rgb
    elif colors.background.color is None:
        backdrop = cell.rgb  # Transparent inline background exposes the cell.
    else:
        backdrop = colors.background.color.rgb
    lighter, darker = sorted((foreground.luminance, backdrop.luminance), reverse=True)
    return (lighter + 0.05) / (darker + 0.05) >= _MINIMUM_CONTRAST


def _linear_component(value: float) -> float:
    return value / 12.92 if value <= _SRGB_BREAKPOINT else ((value + 0.055) / 1.055) ** 2.4
