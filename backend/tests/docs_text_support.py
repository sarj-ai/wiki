from app.document_transform.brand import DocsRequest


def apply_body_text_requests(text: str, requests: list[DocsRequest]) -> str:
    # Apply body deletes and inserts to one paragraph starting at index 1.
    units = text.encode("utf-16-le")
    for request in requests:
        deletion = request.get("deleteContentRange", {}).get("range", {})
        insertion = request.get("insertText", {}).get("location", {})
        if deletion and "segmentId" not in deletion:
            start, end = deletion["startIndex"] - 1, deletion["endIndex"] - 1
            units = units[: 2 * start] + units[2 * end :]
        if "index" in insertion and "segmentId" not in insertion:
            index = insertion["index"] - 1
            inserted = request["insertText"]["text"].encode("utf-16-le")
            units = units[: 2 * index] + inserted + units[2 * index :]
    return units.decode("utf-16-le")
