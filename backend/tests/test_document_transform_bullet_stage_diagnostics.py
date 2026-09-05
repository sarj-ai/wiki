from copy import deepcopy
import json

from app.document_transform.bullet_stage_diagnostics import bullet_stage_counts


def test_snapshot_counts_all_tabs_and_furniture_without_content() -> None:
    paragraph = {"paragraph": {"bullet": {"listId": "SECRET", "textStyle": {"underline": False}}}}
    document = {
        "documentId": "SECRET",
        "tabs": [
            {
                "documentTab": {"body": {"content": [paragraph]}},
                "childTabs": [{"documentTab": {"footers": {"SECRET": {"content": [paragraph]}}}}],
            }
        ],
    }
    before = deepcopy(document)
    counts = bullet_stage_counts(document)
    assert counts["bullet_snapshot_underline_false"] == 2
    assert counts["bullet_snapshot_bold_missing"] == 2
    assert counts["bullet_snapshot_complete"] is True
    assert "SECRET" not in json.dumps(counts)
    assert document == before


def test_snapshot_marks_malformed_values_without_echoing_them() -> None:
    counts = bullet_stage_counts(
        [
            {"paragraph": {"bullet": "SECRET"}},
            {"paragraph": {"bullet": {"textStyle": {"underline": "SECRET", "bold": True}}}},
        ]
    )
    assert counts["bullet_snapshot_underline_malformed"] == 2
    assert counts["bullet_snapshot_bold_true"] == 1
    assert "SECRET" not in json.dumps(counts)


def test_snapshot_traversal_is_bounded_and_explicitly_incomplete() -> None:
    recursive: dict[str, object] = {}
    recursive["cycle"] = recursive
    counts = bullet_stage_counts(recursive)
    assert counts["bullet_snapshot_complete"] is False


def test_snapshot_identifies_loss_without_equating_missing_and_false() -> None:
    before = bullet_stage_counts({"paragraph": {"bullet": {"textStyle": {"underline": False}}}})
    after = bullet_stage_counts({"paragraph": {"bullet": {"textStyle": {}}}})
    assert before["bullet_snapshot_underline_false"] == 1
    assert after["bullet_snapshot_underline_false"] == 0
    assert after["bullet_snapshot_underline_missing"] == 1
