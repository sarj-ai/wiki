from collections import Counter
import re
from types import MappingProxyType

from app.document_transform.quotations import QUOTED_SPAN_PATTERN


_MARKERS = r"after|before|once|when|while|until|unless|if|upon|without"
_IRREGULAR = MappingProxyType(
    {
        "written": "writing",
        "read": "reading",
        "sent": "sending",
        "made": "making",
        "done": "doing",
        "given": "giving",
        "taken": "taking",
        "shown": "showing",
        "known": "knowing",
        "built": "building",
        "found": "finding",
        "held": "holding",
        "kept": "keeping",
        "run": "running",
    }
)
_CONDITION = re.compile(rf"\b(?:{_MARKERS})\s+(?P<clause>[^,.;:!?\n]{{1,240}})", re.IGNORECASE)
_PASSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+"
    r"(?:(?:not|never|already|yet|[a-z]+ly)\s+){0,3}"
    rf"(?P<verb>[a-z]+ed|{'|'.join(_IRREGULAR)})\b",
    re.IGNORECASE,
)
_TEMPORAL_BY = (
    r"\d|then\b|now\b|tomorrow\b|yesterday\b|noon\b|midnight\b|"
    r"(?:mon|tues|wednes|thurs|fri|satur|sun)day\b|"
    r"(?:the\s+)?(?:time|end|start|deadline)\b"
)
_BY_AGENT = re.compile(rf"\bby\s+(?!(?:{_TEMPORAL_BY}))\S", re.IGNORECASE)
_LITERAL = re.compile(rf"```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`|{QUOTED_SPAN_PATTERN}")


def reassigned_conditional_action(source: str, replacement: str) -> bool:
    before = _LITERAL.sub(" ", source)
    after = _LITERAL.sub(" ", replacement)
    removed = _actorless_actions(before) - _actorless_actions(after)
    for verb in removed:
        gerund = _gerund_pattern(verb)
        active = re.compile(
            rf"\b(?:{_MARKERS})\s+(?:not\s+)?(?:[a-z]+ly\s+){{0,2}}{gerund}\b",
            re.IGNORECASE,
        )
        # A controlled participle assigns the action to the containing clause's
        # subject. An actorless passive condition does not establish that actor.
        if len(active.findall(after)) > len(active.findall(before)):
            return True
    return False


def _actorless_actions(text: str) -> Counter[str]:
    actions: Counter[str] = Counter()
    for condition in _CONDITION.finditer(text):
        clause = condition["clause"]
        predicates = list(_PASSIVE.finditer(clause))
        for index, predicate in enumerate(predicates):
            end = predicates[index + 1].start() if index + 1 < len(predicates) else len(clause)
            if not _BY_AGENT.search(clause[predicate.end() : end]):
                actions[predicate["verb"].casefold()] += 1
    return actions


def _gerund_pattern(verb: str) -> str:
    if verb in _IRREGULAR:
        return _IRREGULAR[verb]
    if verb.endswith("ied"):
        return f"{re.escape(verb[:-3])}ying"
    return f"{re.escape(verb[:-2])}e?ing"
