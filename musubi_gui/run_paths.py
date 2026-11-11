"""Readable Windows-safe directory names; identity remains the unique suffix."""

import re
import unicodedata


def job_folder(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", unicodedata.normalize("NFC", name))
    value = re.sub(r"\s+", "-", value).strip(" .-")[:60].rstrip(" .-") or "Job"
    if re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", value, re.IGNORECASE):
        value = "Job-" + value
    return value
