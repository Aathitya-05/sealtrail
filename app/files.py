"""Content-addressed attachment storage: data/files/<sha256>.

The file's name on disk IS its SHA-256. The {filename, sha256, size} triple is
put inside the document content, so the content seal (and therefore every
approval) covers the file too. verify() re-hashes the stored bytes.
"""
import hashlib
import os
import re

from . import db

MAX_BYTES = 10 * 1024 * 1024
_HEX = re.compile(r"^[0-9a-f]{64}$")


def files_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "files")


def path_for(sha: str) -> str:
    if not isinstance(sha, str) or not _HEX.match(sha):
        raise ValueError("bad file hash")
    return os.path.join(files_dir(), sha)


def clean_name(name) -> str:
    n = os.path.basename(str(name or "").replace("\\", "/")).strip()
    n = "".join(ch for ch in n if ch.isprintable() and ch not in '<>:"|?*')
    return n[:120] or "attachment"


def store(data: bytes, filename: str) -> dict:
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"File is larger than {MAX_BYTES // (1024 * 1024)} MB.")
    sha = hashlib.sha256(data).hexdigest()
    p = path_for(sha)
    os.makedirs(files_dir(), exist_ok=True)
    if not os.path.exists(p):
        tmp = p + ".part"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, p)
    return {"filename": clean_name(filename), "sha256": sha, "size": len(data)}


def check(att) -> str:
    """Return '' if the stored file matches the sealed reference, else a reason."""
    if not isinstance(att, dict):
        return "the sealed attachment reference is malformed"
    try:
        p = path_for(att.get("sha256"))
    except ValueError:
        return "the sealed attachment reference is malformed"
    if not os.path.exists(p):
        return f"the stored file '{att.get('filename')}' is missing"
    with open(p, "rb") as f:
        actual = hashlib.sha256(f.read()).hexdigest()
    if actual != att["sha256"]:
        return f"the stored file '{att.get('filename')}' was replaced (its bytes no longer match the sealed SHA-256)"
    return ""


def clear():
    d = files_dir()
    if os.path.isdir(d):
        for n in os.listdir(d):
            try:
                os.remove(os.path.join(d, n))
            except OSError:
                pass
