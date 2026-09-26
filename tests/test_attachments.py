"""Uploads: sealed inside the content, re-hashed by verify(), served only if still intact."""
import hashlib
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app import db, files, tamper
from app import ledger as L
from app import workflow as W
from app.main import app

TOOL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "verify_bundle.py")


def codes(res):
    return {b["code"] for b in res["breaks"]}


def _content(att=None, **kw):
    c = {"title": "PO with quote", "vendor": "Acme", "amount": 1000, "description": "d"}
    if att:
        c["attachment"] = att
    c.update(kw)
    return c


def _doc(conn, data=b"%PDF-1.4 quote v1", name="q.pdf", tpl="quick"):
    att = files.store(data, name)
    return W.create_document(conn, "u_aarav", _content(att), W.TEMPLATES[tpl]["stages"]), att


def test_upload_round_trip(conn):
    with TestClient(app) as c:
        r = c.post("/api/uploads?filename=po.pdf", content=b"%PDF-1.4 hello", headers={"Content-Type": "application/octet-stream"})
        assert r.status_code == 200
        ref = r.json()
        assert ref == {"filename": "po.pdf", "sha256": hashlib.sha256(b"%PDF-1.4 hello").hexdigest(), "size": 14}
        assert open(os.path.join(db.FILES_DIR, ref["sha256"]), "rb").read() == b"%PDF-1.4 hello"


def test_seeded_documents_have_files_and_verify_clean(conn):
    for d in ("DOC-001", "DOC-003", "DOC-002", "DOC-004"):
        assert L.verify(conn, d)["ok"], d
    a1 = W._load_versions(conn, "DOC-003")
    assert a1[0]["content"]["attachment"]["sha256"] != a1[1]["content"]["attachment"]["sha256"]
    assert W._load_versions(conn, "DOC-001")[0]["content"]["attachment"]["filename"].endswith(".pdf")
    assert "attachment" not in W._load_versions(conn, "DOC-002")[0]["content"]


def test_document_with_attachment_verifies_clean_and_is_sealed(conn):
    d, att = _doc(conn)
    assert L.verify(conn, d)["ok"]
    stored = W._load_versions(conn, d)[0]
    assert stored["content"]["attachment"] == att
    bare = {k: v for k, v in stored["content"].items() if k != "attachment"}
    assert stored["content_hash"] == L.content_hash(stored["content"]) != L.content_hash(bare)


def test_reference_to_file_not_in_store_is_refused(conn):
    fake = {"filename": "x.pdf", "sha256": "a" * 64, "size": 3}
    with pytest.raises(W.PolicyError, match="not in the store"):
        W.create_document(conn, "u_aarav", _content(fake), W.TEMPLATES["quick"]["stages"])
    with pytest.raises(W.PolicyError, match="Malformed"):
        W.clean_content(_content({"filename": "x", "sha256": "../../etc/passwd", "size": 1}))
    d, _ = _doc(conn)
    W.perform(conn, d, "u_meera", "REJECT", "no")
    with pytest.raises(W.PolicyError, match="not in the store"):
        W.perform(conn, d, "u_aarav", "RESUBMIT", "again", content=_content(fake))


def test_resubmit_changing_only_the_attachment_is_a_change(conn):
    d, a1 = _doc(conn)
    W.perform(conn, d, "u_meera", "REJECT", "wrong quote")
    a2 = files.store(b"%PDF-1.4 quote v2", "q2.pdf")
    e = W.perform(conn, d, "u_aarav", "RESUBMIT", "new quote", content=_content(a2))
    assert e["payload"]["changed"] == ["attachment"]
    assert L.verify(conn, d)["ok"]
    assert [v["content"]["attachment"]["sha256"] for v in W._load_versions(conn, d)] == [a1["sha256"], a2["sha256"]]


def test_swap_file_tamper_is_file_edited_and_download_refused(conn):
    res = tamper.tamper(conn, "DOC-001", "swap_file")
    assert "uploaded file" in res["message"]
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and codes(r) == {"FILE_EDITED"} and r["first_break"] == 1
    assert r["checks"]["content"] is False and r["checks"]["chain"] and r["checks"]["anchors"]
    with TestClient(app) as c:
        d = c.get("/api/documents/DOC-001/file")
        assert d.status_code == 409 and "sealed at entry #1" in d.json()["detail"]
    with pytest.raises(W.PolicyError, match="no uploaded file"):
        tamper.tamper(conn, "DOC-002", "swap_file")


def test_deleted_file_is_file_missing(conn):
    d, att = _doc(conn)
    os.remove(files.path_for(att["sha256"]))
    r = L.verify(conn, d)
    assert not r["ok"] and codes(r) == {"FILE_MISSING"}


def test_download_intact_file_and_versions(conn):
    with TestClient(app) as c:
        r = c.get("/api/documents/DOC-003/file?version=1")
        assert r.status_code == 200 and r.content.startswith(b"%PDF")
        assert 'attachment; filename="CloudNimbus-renewal-v1.pdf"' in r.headers["content-disposition"]
        assert r.headers["x-content-type-options"] == "nosniff"
        v2 = c.get("/api/documents/DOC-003/file")  # default = current version
        assert v2.content != r.content
        assert c.get("/api/documents/DOC-002/file").status_code == 404
        assert c.get("/api/documents/DOC-003/file?version=9").status_code == 404


def test_bad_uploads_are_rejected_or_sanitized(conn, tmp_path):
    with TestClient(app) as c:
        assert c.post("/api/uploads?filename=evil.exe", content=b"MZ").status_code == 400
        assert c.post("/api/uploads?filename=noext", content=b"x").status_code == 400
        assert c.post("/api/uploads?filename=e.pdf", content=b"").status_code == 400
        big = c.post("/api/uploads?filename=big.pdf", content=b"0" * (files.MAX_BYTES + 1))
        assert big.status_code == 400 and "10 MB" in big.json()["detail"]
        ok = c.post("/api/uploads?filename=../../etc/passwd.pdf", content=b"%PDF sneaky")
        assert ok.status_code == 200 and ok.json()["filename"] == "passwd.pdf"
        ok2 = c.post("/api/uploads?filename=..%5C..%5Cwin%5Cevil.txt", content=b"hello")
        assert ok2.json()["filename"] == "evil.txt"
    stored = set(os.listdir(db.FILES_DIR))
    assert all(len(n) == 64 for n in stored)  # only <sha256> names, nothing escaped or written elsewhere
    assert not os.path.exists(tmp_path / "etc")


def test_bundle_tool_files_flag(conn, tmp_path):
    d, att = _doc(conn)
    bundle = {"format": "sealtrail-bundle/1", "doc_id": d,
              "versions": [{"version": v["version"], "content": v["content"], "content_hash": v["content_hash"]} for v in W._load_versions(conn, d)],
              "ledger": L.load_entries(conn, d), "anchors": [a for a in L.read_anchor_lines() if a["doc_id"] == d]}
    p = tmp_path / "pack.json"
    p.write_text(json.dumps(bundle))
    run = lambda *a: subprocess.run([sys.executable, TOOL, str(p), *a], capture_output=True, text=True)  # noqa: E731
    plain = run()
    assert plain.returncode == 0 and "NOT checked" in plain.stdout
    ok = run("--files", db.FILES_DIR)
    assert ok.returncode == 0 and "Checked 1 attached file" in ok.stdout
    with open(files.path_for(att["sha256"]), "wb") as f:
        f.write(b"swapped")
    bad = run("--files", db.FILES_DIR)
    assert bad.returncode == 1 and "FILE_EDITED" in bad.stdout
    os.remove(files.path_for(att["sha256"]))
    assert "FILE_MISSING" in run("--files", db.FILES_DIR).stdout


def test_reset_recreates_files_dir(conn):
    from app import seed
    conn.close()  # Windows cannot delete an open database file
    db.reset_all()
    assert os.path.isdir(db.FILES_DIR) and os.listdir(db.FILES_DIR) == []
    c2 = db.connect()
    seed.seed(c2)
    c2.close()
    assert len(os.listdir(db.FILES_DIR)) == 3
