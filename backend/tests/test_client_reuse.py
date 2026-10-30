from __future__ import annotations

import threading
from typing import Any

import pytest

from app.drive import service as drive_service
from app.drive.service import reuse_drive_client
from app.models.user import User


def test_same_thread_builds_client_once(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0

    def fake_build(_user: object) -> object:
        nonlocal builds
        builds += 1
        return object()

    monkeypatch.setattr(
        drive_service.GoogleDriveService, "build_drive_client", staticmethod(fake_build)
    )

    clients: dict[int, Any] = {}
    user = User()

    first = reuse_drive_client(user, clients)
    second = reuse_drive_client(user, clients)

    assert first is second
    assert builds == 1


def test_distinct_client_per_worker_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    builds = 0

    def fake_build(_user: object) -> object:
        nonlocal builds
        builds += 1
        return object()

    monkeypatch.setattr(
        drive_service.GoogleDriveService, "build_drive_client", staticmethod(fake_build)
    )

    clients: dict[int, Any] = {}
    user = User()

    def worker() -> None:
        reuse_drive_client(user, clients)
        reuse_drive_client(user, clients)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(clients) == 3
    assert builds == 3
