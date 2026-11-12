import re


_SENTENCE = re.compile(r"[^.!?;]+(?:[.!?;]+|$)")
_FRONTED = re.compile(
    r"\s*(?P<scope>(?:before|after|during|until|since)\s+(?:each|every)\s+"
    r"[A-Za-z]+(?:\s+[A-Za-z]+){0,4})\s*,",
    re.IGNORECASE,
)
_WORD = re.compile(r"[A-Za-z]+")
_SCAFFOLDING = frozenset(
    [
        "a",
        "an",
        "the",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "they",
        "their",
        "them",
        "we",
        "our",
        "you",
        "your",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "has",
        "have",
        "had",
        "do",
        "does",
        "did",
        "and",
        "or",
        "but",
        "also",
        "by",
        "for",
        "from",
        "in",
        "into",
        "of",
        "on",
        "to",
        "with",
        "before",
        "after",
        "during",
        "until",
        "since",
        "each",
        "every",
    ]
)
_INFLECTIONS = ("ing", "ed", "s")
_MIN_INFLECTED_WORD_LENGTH = 5


def fronted_recurring_scope_expanded(source: str, replacement: str) -> bool:
    sentences = tuple(match.group() for match in _SENTENCE.finditer(source))
    for proposed in _SENTENCE.finditer(replacement):
        text = proposed.group()
        fronted = _FRONTED.match(text)
        if fronted is None:
            continue
        scope = " ".join(fronted["scope"].casefold().split())
        bound = tuple(
            sentence for sentence in sentences if scope in " ".join(sentence.casefold().split())
        )
        if not bound:
            continue
        bound_words = _words(" ".join(bound))
        unbound_words = _words(
            " ".join(sentence for sentence in sentences if sentence not in bound)
        )
        if (_words(text[fronted.end() :]) - bound_words) & unbound_words:
            return True
    return False


def _words(text: str) -> frozenset[str]:
    result = set()
    for match in _WORD.finditer(text.casefold()):
        word = match.group()
        if word in _SCAFFOLDING:
            continue
        if len(word) >= _MIN_INFLECTED_WORD_LENGTH:
            for ending in _INFLECTIONS:
                if word.endswith(ending):
                    word = word.removesuffix(ending)
                    break
        result.add(word)
    return frozenset(result)
