"""Generate the native SVG loader from its locked upstream Rive scene."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
from itertools import pairwise
import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Final, NewType

from defusedxml import ElementTree
import typer


if TYPE_CHECKING:
    from xml.etree.ElementTree import Element


_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = Path("frontend/rive/sarj-loader/scene.rml")
_SOURCE_SHA256 = "579cb4c9376a0026422b9904e4491e9eb71b6d7c2a50c521a13e8ea626038b0a"
_GENERATOR = Path("backend/scripts/generate_sarj_loader.py")
_UPSTREAM_COMMIT = "12132f41bb3ef4850f7cad2cc5cab844f60afa35"
_ANIMATED = Path("frontend/public/brand/sarj-loader-12132f41.svg")
_STILL = Path("frontend/public/brand/sarj-loader-still-12132f41.svg")
_PROVENANCE = Path("frontend/rive/sarj-loader/provenance.json")
_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_FRAMES = 360
_FPS = 120
_RESET_FRAME = 326
_BRUSH_COUNT = 93
_GLYPH_COUNT = 2
_WIDTH_KEY_COUNT = 5
_RADIUS_HANDLE_RATIO = 0.5522847498307933
_LINEAR_EASING = "0 0 1 1"
_EXPECTED_TAGS: Final[dict[str, int]] = {
    "Rive": 1,
    "Artboard": 1,
    "LayoutComponentStyle": 1,
    "Node": 1,
    "Shape": 5,
    "Rectangle": 94,
    "PointsPath": 2,
    "StraightVertex": 213,
    "Fill": 3,
    "SolidColor": 3,
    "ClippingShape": 2,
    "StateMachine": 2,
    "StateMachineLayer": 2,
    "AnyState": 2,
    "ExitState": 2,
    "EntryState": 2,
    "StateTransition": 2,
    "AnimationState": 2,
    "LinearAnimation": 2,
    "KeyedObject": 189,
    "KeyedProperty": 191,
    "KeyFrameDouble": 575,
    "CubicEaseInterpolator": 3,
}
RiveObjectId = NewType("RiveObjectId", str)


@dataclass(frozen=True, slots=True)
class Frame:
    frame: float
    value: float
    interpolation: str
    easing: str = _LINEAR_EASING


type Tracks = dict[RiveObjectId, dict[str, tuple[Frame, ...]]]


@dataclass(frozen=True, slots=True)
class WidthTrack:
    start: float
    end: float
    width: float


@dataclass(frozen=True, slots=True)
class Rectangle:
    x: float
    y: float
    height: float
    rotation: float
    radius: float
    track: WidthTrack


@dataclass(frozen=True, slots=True)
class SvgKey:
    frame: float
    value: str


@dataclass(frozen=True, slots=True)
class Asset:
    path: Path
    content: bytes


CLI = typer.Typer(add_completion=False, pretty_exceptions_enable=False, help=__doc__)


def generate_assets(root: Path, *, check: bool) -> None:
    source = _source((root / _SOURCE).read_bytes())
    artboard = _only(source, "Artboard")
    word = _only(artboard, "Node")
    write = next(
        animation
        for animation in artboard.findall("LinearAnimation")
        if animation.get("name") == "Write"
    )
    tracks = _tracks(write)
    assets = (
        Asset(_ANIMATED, _svg(word, tracks, animated=True)),
        Asset(_STILL, _svg(word, tracks, animated=False)),
    )
    provenance = {
        "upstream": {
            "repository": "sarj-ai/platform",
            "commit": _UPSTREAM_COMMIT,
            "path": "products/platform/apps/web/rive/sarj-loader/scene.rml",
        },
        "source": {"path": _SOURCE.as_posix(), "sha256": _SOURCE_SHA256},
        "generator": {
            "path": _GENERATOR.as_posix(),
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
        "format": "SVG/SMIL",
        "duration_seconds": _FRAMES // _FPS,
        "artifacts": [
            {
                "path": asset.path.as_posix(),
                "sha256": hashlib.sha256(asset.content).hexdigest(),
                "bytes": len(asset.content),
            }
            for asset in assets
        ],
    }
    outputs = (*assets, Asset(_PROVENANCE, (json.dumps(provenance, indent=2) + "\n").encode()))
    for asset in outputs:
        path = root / asset.path
        if check:
            _require(
                condition=path.is_file() and path.read_bytes() == asset.content,
                message=f"Generated loader asset is stale: {asset.path}",
            )
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(asset.content)


def _source(content: bytes) -> Element:
    _require(
        condition=hashlib.sha256(content).hexdigest() == _SOURCE_SHA256,
        message="Upstream loader source digest changed; review and version the new source before generating assets",
    )
    root = ElementTree.fromstring(content)
    _validate_scene(root)
    return root


def _validate_scene(root: Element) -> None:
    _require(
        condition=Counter(node.tag for node in root.iter()) == _EXPECTED_TAGS,
        message="Unsupported scene shapes or keyframes",
    )
    _require(
        condition=all(
            color.attrib == {"colorValue": "FF0A0A0A", "name": "Ink"}
            for color in root.iter("SolidColor")
        ),
        message="Unsupported ink color",
    )
    artboard = _only(root, "Artboard")
    _require(
        condition=artboard.get("width") == "544" and artboard.get("height") == "182",
        message="Unsupported artboard dimensions",
    )
    word = _only(artboard, "Node")
    _require(
        condition=word.attrib == {"x": "-22", "y": "-35", "name": "Wordmark", "id": "0:50"},
        message="Unsupported wordmark transform",
    )
    _require(
        condition=all(
            set(brush.attrib) == {"name", "id"}
            for brush in word.findall("Shape")
            if brush.get("id") in {"0:101", "0:164"}
        ),
        message="Unsupported brush transform",
    )
    write = next(
        animation
        for animation in artboard.findall("LinearAnimation")
        if animation.get("name") == "Write"
    )
    _require(
        condition=write.get("duration") == str(_FRAMES)
        and write.get("fps") == str(_FPS)
        and write.get("loopValue") == "loop",
        message="Unsupported animation duration",
    )
    tracks = _tracks(write)
    dot = (
        Frame(0, 0, "hold"),
        Frame(194, 1.18, "cubic", "0.2 0.8 0.2 1"),
        Frame(209, 1, "hold"),
        Frame(326, 0, "hold"),
        Frame(360, 0, "hold"),
    )
    _require(
        condition=tracks[RiveObjectId("0:196")] == {"16": dot, "17": dot},
        message="Unsupported dot animation",
    )
    fade = (
        Frame(0, 1, "hold"),
        Frame(288, 1, "cubic", "0.4 0 0.7 0.2"),
        Frame(326, 0, "hold"),
        Frame(327, 1, "hold"),
        Frame(360, 1, "hold"),
    )
    _require(
        condition=tracks[RiveObjectId("0:50")] == {"18": fade}, message="Unsupported fade animation"
    )
    written = next(
        animation
        for animation in artboard.findall("LinearAnimation")
        if animation.get("name") == "Written"
    )
    still_tracks = _tracks(written)
    for key, width in _widths(tracks).items():
        _require(
            condition=still_tracks[key] == {"20": (Frame(0, width.width, "hold"),)},
            message="Unsupported still brush",
        )
    _require(
        condition=still_tracks[RiveObjectId("0:196")]
        == {"16": (Frame(0, 1, "hold"),), "17": (Frame(0, 1, "hold"),)},
        message="Unsupported still dot",
    )


def _tracks(animation: Element) -> Tracks:
    result = {}
    for node in animation.findall("KeyedObject"):
        key = RiveObjectId(_attribute(node, "objectId"))
        _require(condition=key not in result, message="Duplicate keyed object")
        properties = {}
        for property_node in node.findall("KeyedProperty"):
            name = _attribute(property_node, "propertyKey")
            _require(condition=name not in properties, message="Duplicate keyed property")
            properties[name] = _frames(property_node)
        result[key] = properties
    return result


def _frames(property_node: Element) -> tuple[Frame, ...]:
    result = []
    for key in property_node:
        _require(condition=key.tag == "KeyFrameDouble", message="Unsupported keyframe kind")
        easing = key.find("CubicEaseInterpolator")
        result.append(
            Frame(
                float(_attribute(key, "frame")),
                float(_attribute(key, "value")),
                _attribute(key, "interpolationType"),
                _LINEAR_EASING
                if easing is None
                else " ".join(_attribute(easing, name) for name in ("x1", "y1", "x2", "y2")),
            )
        )
    return tuple(result)


def _widths(
    tracks: Tracks,
) -> dict[RiveObjectId, WidthTrack]:
    result = {}
    for key, properties in tracks.items():
        if "20" not in properties:
            continue
        frames = properties["20"]
        _require(
            condition=len(frames) == _WIDTH_KEY_COUNT, message="Unsupported width track length"
        )
        _require(
            condition=frames[0] == Frame(0, 0, "hold"), message="Unsupported initial brush width"
        )
        _require(
            condition=frames[1].value == 0 and frames[1].interpolation == "linear",
            message="Unsupported brush reveal",
        )
        _require(
            condition=frames[2].interpolation == "hold" and frames[2].value > 0,
            message="Unsupported final brush width",
        )
        _require(
            condition=frames[3:] == (Frame(326, 0, "hold"), Frame(360, 0, "hold")),
            message="Unsupported brush reset",
        )
        _require(
            condition=0 <= frames[1].frame < frames[2].frame < _RESET_FRAME,
            message="Invalid brush frame order",
        )
        result[key] = WidthTrack(frames[1].frame, frames[2].frame, frames[2].value)
    _require(condition=len(result) == _BRUSH_COUNT, message="Unexpected brush count")
    return result


def _svg(
    word: Element,
    tracks: Tracks,
    *,
    animated: bool,
) -> bytes:
    widths = _widths(tracks)
    svg = ElementTree.fromstring(b"<svg />")
    svg.attrib.update(
        {
            "xmlns": _SVG_NAMESPACE,
            "viewBox": "0 0 544 182",
            "width": "544",
            "height": "182",
            "fill": "#0a0a0a",
        },
    )
    definitions = _child(svg, "defs")
    brushes = [shape for shape in word.findall("Shape") if shape.get("id") in {"0:101", "0:164"}]
    _require(
        condition=[shape.get("id") for shape in brushes] == ["0:101", "0:164"],
        message="Unsupported brush order",
    )
    for index, brush in enumerate(brushes):
        clip = _child(
            definitions, "clipPath", {"id": f"brush-{index}", "clipPathUnits": "userSpaceOnUse"}
        )
        for rectangle in brush.findall("Rectangle"):
            _brush_path(clip, _rectangle(rectangle, widths), animated=animated)
    group = _child(svg, "g", {"transform": "translate(-22 -35)"})
    if animated:
        _scalar_animation(group, tracks[RiveObjectId("0:50")]["18"], transform=False)
    _draw_glyphs(group, word, brushes)
    _draw_dot(group, word, tracks, animated=animated)
    serialized = ElementTree.tostring(svg, encoding="utf-8")
    if not isinstance(serialized, bytes):
        message = "SVG serialization did not return bytes"
        raise TypeError(message)
    return serialized + b"\n"


def _draw_glyphs(group: Element, word: Element, brushes: list[Element]) -> None:
    glyphs = [shape for shape in word.findall("Shape") if shape.find("PointsPath") is not None]
    _require(condition=len(glyphs) == _GLYPH_COUNT, message="Unexpected glyph count")
    for index, shape in enumerate(glyphs):
        _require(condition=set(shape.attrib) == {"name"}, message="Unsupported glyph transform")
        clip = _only(shape, "ClippingShape")
        _require(
            condition=set(clip.attrib) == {"sourceId", "name"}
            and clip.get("sourceId") == brushes[index].get("id"),
            message="Unsupported glyph clip",
        )
        path = _only(shape, "PointsPath")
        _require(
            condition=set(path.attrib) == {"isClosed", "name"} and path.get("isClosed") == "true",
            message="Unsupported glyph path",
        )
        _require(
            condition=all(
                vertex.tag == "StraightVertex" and set(vertex.attrib) == {"x", "y"}
                for vertex in path
            ),
            message="Unsupported glyph vertex",
        )
        points = [_attribute(vertex, "x") + " " + _attribute(vertex, "y") for vertex in path]
        _child(
            group,
            "path",
            {
                "d": "M" + points[0] + "L" + " ".join(points[1:]) + "Z",
                "clip-path": f"url(#brush-{index})",
            },
        )


def _draw_dot(group: Element, word: Element, tracks: Tracks, *, animated: bool) -> None:
    dot = next(shape for shape in word.findall("Shape") if shape.get("name") == "Nuqta")
    _require(
        condition=set(dot.attrib) == {"x", "y", "rotation", "scaleX", "scaleY", "name", "id"}
        and dot.get("scaleX") == dot.get("scaleY") == "0",
        message="Unsupported dot transform",
    )
    fixed = _child(
        group,
        "g",
        {
            "transform": f"translate({_attribute(dot, 'x')} {_attribute(dot, 'y')}) rotate({_number(math.degrees(float(_attribute(dot, 'rotation'))))})"
        },
    )
    scale = _child(fixed, "g", {"transform": "scale(0)" if animated else "scale(1)"})
    if animated:
        _scalar_animation(scale, tracks[RiveObjectId("0:196")]["16"], transform=True)
    rectangle = _only(dot, "Rectangle")
    _require(
        condition=set(rectangle.attrib) == {"width", "height", "name"},
        message="Unsupported dot geometry",
    )
    width, height = float(_attribute(rectangle, "width")), float(_attribute(rectangle, "height"))
    _child(
        scale,
        "rect",
        {
            "x": _number(-width / 2),
            "y": _number(-height / 2),
            "width": _number(width),
            "height": _number(height),
        },
    )


def _rectangle(node: Element, widths: dict[RiveObjectId, WidthTrack]) -> Rectangle:
    _require(
        condition=node.get("originX") == "0" and node.get("originY") == "0.5",
        message="Unsupported brush origin",
    )
    radii = [
        float(_attribute(node, "cornerRadius" + corner)) for corner in ("TL", "TR", "BL", "BR")
    ]
    _require(condition=len(set(radii)) == 1, message="Unsupported asymmetric brush corners")
    return Rectangle(
        float(_attribute(node, "x")),
        float(_attribute(node, "y")),
        float(_attribute(node, "height")),
        float(_attribute(node, "rotation")),
        radii[0],
        widths[RiveObjectId(_attribute(node, "id"))],
    )


def _brush_path(parent: Element, rectangle: Rectangle, *, animated: bool) -> None:
    track = rectangle.track
    final = _rounded_path(track.width, rectangle)
    zero = _rounded_path(0, rectangle)
    node = _child(
        parent,
        "path",
        {
            "d": zero if animated else final,
            "transform": f"translate({_number(rectangle.x)} {_number(rectangle.y)}) rotate({_number(math.degrees(rectangle.rotation))})",
        },
    )
    if not animated:
        return
    keys = [SvgKey(0, zero), SvgKey(track.start, zero)]
    cap = min(rectangle.height, 2 * rectangle.radius)
    if track.width > cap:
        frame = track.start + (track.end - track.start) * cap / track.width
        keys.append(SvgKey(frame, _rounded_path(cap, rectangle)))
    keys.extend(
        (SvgKey(track.end, final), SvgKey(326, final), SvgKey(326, zero), SvgKey(360, zero))
    )
    _child(node, "animate", {"attributeName": "d", **_animation_attributes(tuple(keys))})


def _rounded_path(width: float, rectangle: Rectangle) -> str:
    top = -rectangle.height / 2
    bottom = rectangle.height / 2
    # Match Rive's uniform corner clamp and cubic quarter-circle handles.
    radius = min(rectangle.radius, width / 2, rectangle.height / 2)
    handle = radius * (1 - _RADIUS_HANDLE_RATIO)
    return (
        f"M0 {_number(top + radius)} "
        f"C0 {_number(top + handle)} {_number(handle)} {_number(top)} {_number(radius)} {_number(top)} "
        f"L{_number(width - radius)} {_number(top)} "
        f"C{_number(width - handle)} {_number(top)} {_number(width)} {_number(top + handle)} {_number(width)} {_number(top + radius)} "
        f"L{_number(width)} {_number(bottom - radius)} "
        f"C{_number(width)} {_number(bottom - handle)} {_number(width - handle)} {_number(bottom)} {_number(width - radius)} {_number(bottom)} "
        f"L{_number(radius)} {_number(bottom)} "
        f"C{_number(handle)} {_number(bottom)} 0 {_number(bottom - handle)} 0 {_number(bottom - radius)} Z"
    )


def _scalar_animation(parent: Element, frames: tuple[Frame, ...], *, transform: bool) -> None:
    keys = [SvgKey(frames[0].frame, _number(frames[0].value))]
    splines = []
    for current, following in pairwise(frames):
        if current.interpolation == "hold" and current.value != following.value:
            keys.append(SvgKey(following.frame, _number(current.value)))
            splines.append(_LINEAR_EASING)
        keys.append(SvgKey(following.frame, _number(following.value)))
        splines.append(current.easing if current.interpolation == "cubic" else _LINEAR_EASING)
    attributes = _animation_attributes(tuple(keys), tuple(splines))
    attributes["attributeName"] = "transform" if transform else "opacity"
    if transform:
        attributes["type"] = "scale"
    _child(parent, "animateTransform" if transform else "animate", attributes)


def _animation_attributes(
    keys: tuple[SvgKey, ...], splines: tuple[str, ...] = ()
) -> dict[str, str]:
    result = {
        "dur": "3s",
        "repeatCount": "indefinite",
        "calcMode": "spline" if splines else "linear",
        "values": ";".join(key.value for key in keys),
        "keyTimes": ";".join(_number(key.frame / _FRAMES) for key in keys),
    }
    if splines:
        _require(condition=len(splines) == len(keys) - 1, message="Invalid spline count")
        result["keySplines"] = ";".join(splines)
    return result


def _child(parent: Element, tag: str, attributes: dict[str, str] | None = None) -> Element:
    node = parent.makeelement(tag, {} if attributes is None else attributes)
    parent.append(node)
    return node


def _require(*, condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _attribute(element: Element, name: str) -> str:
    value = element.get(name)
    if value is None:
        message = f"Missing {name} on {element.tag}"
        raise ValueError(message)
    return value


def _only(parent: Element, tag: str) -> Element:
    found = parent.findall(tag)
    _require(condition=len(found) == 1, message=f"Expected exactly one {tag}")
    return found[0]


def _number(value: float) -> str:
    return f"{value:.8f}".rstrip("0").rstrip(".") or "0"


@CLI.command()
def main(
    *,
    check: Annotated[
        bool, typer.Option("--check", help="Verify generated assets without writing")
    ] = False,
) -> None:
    generate_assets(_ROOT, check=check)


if __name__ == "__main__":
    CLI()
