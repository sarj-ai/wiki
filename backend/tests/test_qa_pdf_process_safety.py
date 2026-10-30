from pathlib import Path
import subprocess

import pytest

from scripts import qa_document_transform_corpus as corpus


def test_pdf_inspection_uses_fixed_argv_without_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf = tmp_path / "private; document.pdf"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def which(name: str) -> str:
        assert name == "pdfinfo"
        return "/usr/bin/pdfinfo"

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, options))
        return subprocess.CompletedProcess(
            command, 0, stdout="Pages: 1\nPage size: 612 x 792 pts\n", stderr=""
        )

    monkeypatch.setattr(corpus.shutil, "which", which)
    monkeypatch.setattr(corpus.subprocess, "run", run)
    assert corpus._read_pdf_info(pdf) == corpus.PdfInfo(
        pages=1, page_sizes_points=((612.0, 792.0),)
    )
    assert calls == [
        (
            ["/usr/bin/pdfinfo", "-box", str(pdf)],
            {"check": False, "capture_output": True, "text": True, "timeout": 10},
        )
    ]


@pytest.mark.parametrize("expected", ["25.06.0", "wrong; version"], ids=["pinned", "mismatch"])
def test_version_probe_never_executes_expected_version(
    expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def which(name: str) -> str:
        assert name in {"pdftoppm", "pdfinfo"}
        return f"/usr/bin/{name}"

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, options))
        return subprocess.CompletedProcess(
            command, 0, stdout="", stderr="pdftoppm version 25.06.0\n"
        )

    monkeypatch.setattr(corpus.shutil, "which", which)
    monkeypatch.setattr(corpus.subprocess, "run", run)
    if expected == "25.06.0":
        assert corpus._require_pdftoppm(expected) == expected
    else:
        with pytest.raises(corpus.QaError, match="explicitly pinned version"):
            corpus._require_pdftoppm(expected)
    assert calls == [
        (
            ["/usr/bin/pdftoppm", "-v"],
            {"check": False, "capture_output": True, "text": True, "timeout": 10},
        )
    ]


def test_renderer_uses_fixed_argv_and_redacts_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pdf = tmp_path / "private; document.pdf"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def which(name: str) -> str:
        assert name == "pdftoppm"
        return "/usr/bin/pdftoppm"

    def info(path: Path) -> corpus.PdfInfo:
        assert path == pdf
        return corpus.PdfInfo(pages=1, page_sizes_points=((612.0, 792.0),))

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, options))
        raise subprocess.TimeoutExpired(command, 120, output=b"private document text")

    monkeypatch.setattr(corpus.shutil, "which", which)
    monkeypatch.setattr(corpus, "_read_pdf_info", info)
    monkeypatch.setattr(corpus.subprocess, "run", run)
    with pytest.raises(corpus.QaError, match=r"^pdftoppm exceeded the bounded render timeout$"):
        corpus._render_pdf(pdf, capture_dir=tmp_path, stem="source")
    assert len(calls) == 1
    command, options = calls[0]
    assert command[:-1] == ["/usr/bin/pdftoppm", "-png", "-r", "144", str(pdf)]
    prefix = Path(command[-1])
    assert prefix.name == "source"
    assert prefix.parent.parent == tmp_path
    assert not prefix.parent.exists()
    assert options == {"check": False, "capture_output": True, "timeout": 120}


def test_inspection_timeout_is_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str:
        assert name == "pdfinfo"
        return "/usr/bin/pdfinfo"

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        assert options["timeout"] == 10
        raise subprocess.TimeoutExpired(command, 10, output="private document text")

    monkeypatch.setattr(corpus.shutil, "which", which)
    monkeypatch.setattr(corpus.subprocess, "run", run)
    with pytest.raises(corpus.QaError, match=r"^pdfinfo exceeded the bounded inspection timeout$"):
        corpus._read_pdf_info(Path("private.pdf"))
