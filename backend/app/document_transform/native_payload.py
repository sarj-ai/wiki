from __future__ import annotations

from pydantic import JsonValue


def canonical_style(value: JsonValue) -> JsonValue:
    # Docs omits zero dimensions and serializes colors as float32. Normalize
    # only style representation; document text and numeric data stay exact.
    match value:
        case dict():
            result = {key: canonical_style(child) for key, child in value.items()}
            if result.get("unit") == "PT":
                result.setdefault("magnitude", 0)
            return result
        case list():
            return [canonical_style(child) for child in value]
        case float():
            return round(value, 6)
        case _:
            return value


def stable_native_payload(value: JsonValue) -> JsonValue:
    match value:
        case dict():
            return {
                key: (
                    {field: item for field, item in child.items() if field != "contentUri"}
                    if key == "imageProperties" and isinstance(child, dict)
                    else stable_native_payload(child)
                )
                for key, child in value.items()
            }
        case list():
            return [stable_native_payload(child) for child in value]
        case _:
            return value
