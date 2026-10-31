"""Remove credentials from text before it is logged or stored."""

import os
import re


def redact(text: str) -> str:
    for key, value in os.environ.items():
        if re.search("token|password|secret|api_key", key, re.I) and len(value) >= 6:
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"hf_[A-Za-z0-9]{16,}", "[REDACTED]", text)
    text = re.sub(r"(https?://)[^\s/@]+:[^\s/@]+@", r"\1[REDACTED]@", text)
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[REDACTED_QUERY]", text)
    return re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1[REDACTED]", text)
