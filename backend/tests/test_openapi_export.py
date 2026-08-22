from pathlib import Path
import sys

import pytest

from scripts import export_openapi


def test_render_is_deterministic() -> None:
    assert export_openapi._render_spec() == export_openapi._render_spec()


def test_check_does_not_modify_stale_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "openapi.json"
    output.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["export_openapi.py", "--check", str(output)])

    assert export_openapi.main() == 1
    assert output.read_text(encoding="utf-8") == "stale"


def test_export_then_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "openapi.json"
    monkeypatch.setattr(sys, "argv", ["export_openapi.py", str(output)])
    assert export_openapi.main() == 0

    monkeypatch.setattr(sys, "argv", ["export_openapi.py", "--check", str(output)])
    assert export_openapi.main() == 0
