from __future__ import annotations

import hashlib
import json
from pathlib import Path

from defusedxml import ElementTree
import pytest

from scripts import generate_sarj_loader as loader


@pytest.fixture
def loader_project(tmp_path: Path) -> Path:
    source = tmp_path / loader._SOURCE
    source.parent.mkdir(parents=True)
    source.write_bytes((loader._ROOT / loader._SOURCE).read_bytes())
    loader.generate_assets(tmp_path, check=False)
    return tmp_path


def test_generated_assets_are_current(loader_project: Path) -> None:
    loader.generate_assets(loader_project, check=True)
    provenance = json.loads((loader_project / loader._PROVENANCE).read_text())
    assert provenance["source"]["sha256"] == loader._SOURCE_SHA256
    assert provenance["duration_seconds"] == 3
    for artifact in provenance["artifacts"]:
        path = Path(artifact["path"])
        assert not path.is_absolute()
        content = (loader_project / path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == artifact["sha256"]
        assert len(content) == artifact["bytes"]


def test_check_rejects_changed_asset_without_repairing_it(loader_project: Path) -> None:
    asset = loader_project / loader._ANIMATED
    changed = asset.read_bytes().replace(b"#0a0a0a", b"#ff0000")
    asset.write_bytes(changed)
    with pytest.raises(ValueError, match="Generated loader asset is stale"):
        loader.generate_assets(loader_project, check=True)
    assert asset.read_bytes() == changed


def test_source_drift_fails_before_asset_writes(loader_project: Path) -> None:
    asset = loader_project / loader._ANIMATED
    before = asset.read_bytes()
    source = loader_project / loader._SOURCE
    source.write_bytes(source.read_bytes().replace(b'width="544"', b'width="545"', 1))
    with pytest.raises(ValueError, match="Upstream loader source digest changed"):
        loader.generate_assets(loader_project, check=False)
    assert asset.read_bytes() == before


def test_check_rejects_changed_provenance(loader_project: Path) -> None:
    provenance = loader_project / loader._PROVENANCE
    changed = provenance.read_bytes().replace(b'"SVG/SMIL"', b'"unreviewed"')
    provenance.write_bytes(changed)
    with pytest.raises(ValueError, match="Generated loader asset is stale"):
        loader.generate_assets(loader_project, check=True)
    assert provenance.read_bytes() == changed


@pytest.mark.parametrize(
    ("original", "replacement", "message"),
    [
        (
            b'<Shape name="Sin-ra brush"',
            b'<Shape scaleX="2" name="Sin-ra brush"',
            "Unsupported brush transform",
        ),
        (b'colorValue="FF0A0A0A"', b'colorValue="FFFF0000"', "Unsupported ink color"),
    ],
)
def test_new_scenes_reject_unsupported_geometry(
    loader_project: Path,
    original: bytes,
    replacement: bytes,
    message: str,
) -> None:
    source = loader_project / loader._SOURCE
    changed = source.read_bytes().replace(original, replacement, 1)
    with pytest.raises(ValueError, match=message):
        loader._validate_scene(ElementTree.fromstring(changed))
