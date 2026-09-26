"""Content-addressed attachment storage: <db.FILES_DIR>/<sha256>.

The file's name on disk IS its SHA-256 (so there is no path traversal: user input
never becomes a path). The {filename, sha256, size} triple lives inside the document
content, so the content seal (and every approval) covers the file. verify() re-hashes
the stored bytes. Files are only ever served as downloads, never inline.
"""
import hashlib
import os
import re

from . import db

MAX_BYTES = 10 * 1024 * 1024
ALLOWED_EXT = ("pdf", "docx", "xlsx", "pptx", "png", "jpg", "jpeg", "txt")
_HEX = re.compile(r"^[0-9a-f]{64}$")


def path_for(sha: str) -> str:
    if not isinstance(sha, str) or not _HEX.match(sha):
        raise ValueError("bad file hash")
    return os.path.join(db.FILES_DIR, sha)


def clean_name(name) -> str:
    """Basename only, no separators, no control characters, max 120 chars."""
    n = os.path.basename(str(name or "").replace("\\", "/")).strip()
    n = "".join(ch for ch in n if ch.isprintable() and ch not in '<>:"|?*/\\')
    return n[-120:] if len(n) > 120 else n


def store(data: bytes, filename: str) -> dict:
    """Validate and save an upload. Raises ValueError with a user-facing message."""
    name = clean_name(filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type not allowed. Use one of: {', '.join(ALLOWED_EXT)}.")
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    sha = hashlib.sha256(data).hexdigest()
    p = path_for(sha)
    os.makedirs(db.FILES_DIR, exist_ok=True)
    if not os.path.exists(p):
        tmp = p + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    return {"filename": name, "sha256": sha, "size": len(data)}


def state(att) -> str:
    """'ok', 'missing' or 'edited' for a sealed attachment reference."""
    try:
        p = path_for((att or {}).get("sha256"))
    except ValueError:
        return "edited"
    if not os.path.exists(p):
        return "missing"
    with open(p, "rb") as f:
        return "ok" if hashlib.sha256(f.read()).hexdigest() == att["sha256"] else "edited"
