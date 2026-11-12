import pytest

from app.document_transform.safety import rewrite_constraints
from tests.test_editorial_framing_constraints import _decision


_FACT = (
    "the cache estimate is 1 1/2 ms. The disk estimate is 3,50 ms. "
    "The network uncertainty is ± 0.05 s. The value 1.20e-3 s retains "
    "meaningful precision; missing values remain missing."
)


@pytest.mark.parametrize("table", ["the table", "this table"])
@pytest.mark.parametrize("connector", ["that ", ""])
@pytest.mark.parametrize("context", ["", "Keep all source values. "])
@pytest.mark.parametrize("reading", ["To interpret", "For the purposes of interpreting"])
def test_table_attention_frame_can_go_without_changing_its_facts(
    table: str, connector: str, context: str, reading: str
) -> None:
    source = f"{context}{reading} {table}, it should be noted {connector}{_FACT}"
    proposed = f"{context}The cache{_FACT.removeprefix('the cache')}"

    assert list(_decision(source, proposed).accepted.values()) == [proposed]


@pytest.mark.parametrize("connector", ["that ", ""])
@pytest.mark.parametrize(
    "fact", ["the estimate may vary under load.", "the team must retain every original value."]
)
def test_removing_attention_preserves_the_following_modal(connector: str, fact: str) -> None:
    source = f"To interpret the table, it should be noted {connector}{fact}"
    proposed = f"The{fact.removeprefix('the')}"

    assert list(_decision(source, proposed).accepted.values()) == [proposed]


@pytest.mark.parametrize("connector", ["that ", ""])
@pytest.mark.parametrize(
    ("fact", "strengthened"),
    [
        ("the estimate may vary under load.", "The estimate varies under load."),
        ("the team must retain every original value.", "The team retains every original value."),
    ],
)
def test_attention_cleanup_cannot_erase_the_following_modal(
    connector: str, fact: str, strengthened: str
) -> None:
    decision = _decision(
        f"To interpret the table, it should be noted {connector}{fact}", strengthened
    )

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


@pytest.mark.parametrize(
    "prefix",
    [
        "",
        "To interpret the table, ",
        "To interpret this table, ",
        "For the purposes of interpreting the table, ",
        "For the purposes of interpreting this table, ",
    ],
)
@pytest.mark.parametrize("recording", ["in the log", "by the operator"])
def test_real_recording_instructions_remain_obligations(prefix: str, recording: str) -> None:
    source = f"{prefix}It should be noted {recording} that the cache estimate is 1 1/2 ms."
    decision = _decision(source, "The cache estimate is 1 1/2 ms.")

    assert not decision.accepted
    assert [finding.code for finding in decision.findings] == ["force_language_changed"]


@pytest.mark.parametrize("delimiter", ['"', "`"])
def test_quoted_or_code_attention_text_is_not_an_editorial_exception(delimiter: str) -> None:
    text = "To interpret the table, it should be noted that the cache estimate is 1 1/2 ms."
    source = f"The exact text is {delimiter}{text}{delimiter}"

    assert "should" in rewrite_constraints(source).force_language
    assert not _decision(source, "The cache estimate is 1 1/2 ms.").accepted


def test_original_live_table_frame_can_go_with_every_quantity_preserved() -> None:
    source = (
        "For the purposes of interpreting the table, it should be noted that the cache "
        "estimate is 1 1/2 ms. The disk estimate is 3,50 ms. The network uncertainty is "
        "± 0.05 s. The value 1.20e-3 s retains its meaningful precision; missing values "
        "remain missing."
    )
    proposed = f"The cache{_FACT.removeprefix('the cache')}"

    assert list(_decision(source, proposed).accepted.values()) == [proposed]
