import unicodedata


def first_strong_is_rtl(text: str) -> bool:
    for character in text:
        direction = unicodedata.bidirectional(character)
        if direction in {"R", "AL"}:
            return True
        if direction == "L":
            return False
    return False
