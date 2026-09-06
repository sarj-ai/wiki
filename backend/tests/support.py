from typing import Any, Self


def dependency(value: object) -> Any:
    return value


class WordDriveFake:
    def files(self) -> Self:
        return self

    def get_media(self, **kwargs: Any) -> object:
        return object()


class WordCreateDriveFake(WordDriveFake):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        return object()
