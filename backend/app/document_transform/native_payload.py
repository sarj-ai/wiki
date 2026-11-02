from __future__ import annotations

from pydantic import JsonValue


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
