"""Payload-only redaction. Never inspect credential files or the environment."""
import re

MASK = "[REDACTED]"
PATTERNS = [
    re.compile(r"-----BEGIN (?:[A-Z0-9 ]* )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z0-9 ]* )?PRIVATE KEY-----|$)"),
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9_]{12,}|github_pat_[A-Za-z0-9_]+|AKIA[A-Z0-9]{16})\b"),
    # Treat every shell/.env assignment as sensitive, including multiline quotes.
    re.compile(r"""(?m)(\b(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*\s*=\s*)(?:"[^"]*"|'[^']*'|[^\r\n]*)"""),
    re.compile(r"""(?i)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|authorization)["']?\s*:\s*)(?:"[^"]*"|'[^']*'|[^,\r\n}]+)"""),
    re.compile(r"(?i)\bBearer\s+\S+"),
]

def redact(value):
    if isinstance(value, str):
        for i, pattern in enumerate(PATTERNS):
            value = pattern.sub((lambda m: m[1] + MASK) if i in (3, 4) else MASK, value)
        return value
    if isinstance(value, dict):
        return {redact(k): MASK if re.search(r"(?i)(api.?key|token|secret|password|authorization)", str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value
