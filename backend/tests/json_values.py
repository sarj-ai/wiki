from pydantic import JsonValue


def _value_at(value: JsonValue, path: tuple[str | int, ...]) -> JsonValue:
    for key in path:
        if isinstance(key, str):
            assert isinstance(value, dict)
            value = value[key]
        else:
            assert isinstance(value, list)
            value = value[key]
    return value


def json_object_at(value: JsonValue, *path: str | int) -> dict[str, JsonValue]:
    result = _value_at(value, path)
    assert isinstance(result, dict)
    return result


def json_array_at(value: JsonValue, *path: str | int) -> list[JsonValue]:
    result = _value_at(value, path)
    assert isinstance(result, list)
    return result
