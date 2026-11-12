import json
from pathlib import Path

from app.document_transform.google_gateway import post_mutation_marker_range


def test_marker_geometry_matches_captured_google_native_date_range_readback() -> None:
    data = Path(__file__).parent / "data" / "native_chips"
    before = json.loads((data / "before.json").read_text())
    after = json.loads((data / "after.json").read_text())
    marker = {"tabId": "t.0", "startIndex": 129, "endIndex": 130}
    requests = []
    for start, end, timestamp in (
        (121, 128, "2026-11-15T02:00:00Z"),
        (97, 120, "2026-11-15T00:30:00Z"),
    ):
        requests.extend(
            [
                {
                    "deleteContentRange": {
                        "range": {"tabId": "t.0", "startIndex": start, "endIndex": end}
                    }
                },
                {
                    "insertDate": {
                        "location": {"tabId": "t.0", "index": start},
                        "dateElementProperties": {
                            "timestamp": timestamp,
                            "timeZoneId": "America/New_York",
                            "locale": "en",
                            "dateFormat": "DATE_FORMAT_ISO8601",
                            "timeFormat": "TIME_FORMAT_HOUR_MINUTE_TIMEZONE",
                        },
                    }
                },
            ]
        )
    observed = after["tabs"][0]["documentTab"]["namedRanges"][
        "sarj_native_date_range_canary_20261112"
    ]["namedRanges"][0]["ranges"][0]
    assert before["tabs"][0]["documentTab"]["body"]["content"][-1]["endIndex"] == marker["endIndex"]
    assert post_mutation_marker_range(marker, requests) == {"tabId": "t.0", **observed}
    assert observed == {"startIndex": 101, "endIndex": 102}
