"""Treat blank / whitespace / known placeholders as incomplete business addresses."""
import re

_PLACEHOLDER_PATTERNS = [
    re.compile(r'^main\s*address$', re.I),
    re.compile(r'^123\s+main\s+(st|street|road)\b', re.I),
    re.compile(r'^e\.?\s*g\.?\b', re.I),
    re.compile(r'^n/?a$', re.I),
    re.compile(r'^address$', re.I),
    re.compile(r'^your\s+address', re.I),
    re.compile(r'^enter\s+', re.I),
    re.compile(r'^test\s*address$', re.I),
    re.compile(r'^placeholder$', re.I),
]


def is_meaningful_address(value) -> bool:
    text = (value or '').strip()
    if not text or len(text) < 5:
        return False
    for pat in _PLACEHOLDER_PATTERNS:
        if pat.search(text):
            return False
    return True
