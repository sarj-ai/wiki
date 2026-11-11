import re


_REVISION_ARGUMENT = re.compile(
    r"\b(?:with|using|use)\s+(?:(?:the|a|an)\s+)?"
    r"(?:(?P<reference>(?:required|current|expected|matching|source|specified|previous|latest)"
    r"\s+revision(?![-\u2011\s]+(?:control|history|tracking|management)\b)(?:\s+id)?)\b|"
    r"(?P<ticks>`*)(?P<identifier>requiredRevisionId|revisionId)\b(?P=ticks))",
    re.IGNORECASE,
)


def revision_arguments(text: str) -> tuple[str, ...]:
    return tuple(
        match.group("identifier") or " ".join(match.group("reference").casefold().split())
        for match in _REVISION_ARGUMENT.finditer(text)
    )
