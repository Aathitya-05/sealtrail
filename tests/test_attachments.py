"""File attachments: sealed inside the content, re-hashed by verify()."""
import hashlib
import os

import pytest
from fastapi.testclient import TestClient

from app import files, tamper
from app import ledger as L
from app import workflow as W
from app.main import app


def codes(res):
    return {b["code"] for b in res["breaks"]}


def _content(att):
    return {"title": "PO with quote", "vendor": "Acme", "amount": 1000, "description": "d", "attachment": att}


def test_seeded_attachment_verifies_and_is_content_addressed(conn):
    v = W.document_view(conn, "DOC-001")
    att = v["content"]["attachment"]
    assert att["sha256"] and files.check(att) == ""
    assert files.path_for(att["sha256"]).endswith(att["sha256"])
    assert L.verify(conn, "DOC-001")["ok"]


def test_attachment_is_inside_the_content_seal(conn):
    att = files.store(b"quote v1", "q.txt")
    d = W.create_document(conn, "u_aarav", _content(att), W.TEMPLATES["quick"]["stages"])
    stored = W._load_versions(conn, d)[0]
    assert stored["content"]["attachment"] == att
    assert stored["content_hash"] == L.content_hash(stored["content"])
    bare = {k: v for k, v in stored["content"].items() if k != "attachment"}
    assert stored["content_hash"] != L.content_hash(bare)


def test_replacing_the_stored_file_is_caught_as_content_edited(conn):
    att = files.store(b"quote v1", "q.txt")
    d = W.create_document(conn, "u_aarav", _content(att), W.TEMPLATES["quick"]["stages"])
    W.perform(conn, d, "u_meera", "APPROVE", "ok")
    assert L.verify(conn, d)["ok"]
    with open(files.path_for(att["sha256"]), "wb") as f:  # attacker swaps the PDF, touches nothing else
        f.write(b"a different quote")
    r = L.verify(conn, d)
    assert not r["ok"] and codes(r) == {"CONTENT_EDITED"}
    assert r["first_break"] == 1 and r["checks"]["chain"] and r["checks"]["anchors"]


def test_deleted_file_is_caught(conn):
    att = files.store(b"quote v1", "q.txt")
    d = W.create_document(conn, "u_aarav", _content(att), W.TEMPLATES["quick"]["stages"])
    os.remove(files.path_for(att["sha256"]))
    assert "CONTENT_EDITED" in codes(L.verify(conn, d))


def test_tamper_lab_replace_file(conn):
    tamper.tamper(conn, "DOC-001", "replace_file")
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and "CONTENT_EDITED" in codes(r)
    with pytest.raises(W.PolicyError):
        tamper.tamper(conn, "DOC-004", "replace_file")  # no attachment there


def test_attachment_must_exist_and_match(conn):
    fake = {"filename": "x.pdf", "sha256": "a" * 64, "size": 3}
    with pytest.raises(W.PolicyError, match="Attachment rejected"):
        W.create_document(conn, "u_aarav", _content(fake), W.TEMPLATES["quick"]["stages"])
    with pytest.raises(W.PolicyError):
        W.clean_content(_content({"filename": "x", "sha256": "../../etc/passwd", "size": 1}))


def test_resubmit_with_new_file_makes_new_sealed_version(conn):
    a1 = files.store(b"quote v1", "q1.txt")
    d = W.create_document(conn, "u_aarav", _content(a1), W.TEMPLATES["quick"]["stages"])
    W.perform(conn, d, "u_meera", "REJECT", "wrong quote")
    a2 = files.store(b"quote v2", "q2.txt")
    W.perform(conn, d, "u_aarav", "RESUBMIT", "new quote", content=_content(a2))
    vs = W._load_versions(conn, d)
    assert [v["content"]["attachment"]["sha256"] for v in vs] == [a1["sha256"], a2["sha256"]]
    assert L.verify(conn, d)["ok"]
    with open(files.path_for(a1["sha256"]), "wb") as f:  # swapping the OLD version's file is caught too
        f.write(b"x")
    r = L.verify(conn, d)
    assert not r["ok"] and r["first_break"] == 1


def test_upload_and_download_endpoints(conn):
    with TestClient(app) as c:
        up = c.post("/api/files?filename=..%5Cevil/po.pdf", content=b"%PDF-1.4 hello")
        assert up.status_code == 200
        ref = up.json()
        assert ref["sha256"] == hashlib.sha256(b"%PDF-1.4 hello").hexdigest()
        assert ref["filename"] == "po.pdf" and ref["size"] == 14
        r = c.post("/api/documents", json={"owner": "u_aarav", "template": "quick", "content": _content(ref)})
        assert r.status_code == 200, r.text
        doc = r.json()["id"]
        dl = c.get(f"/api/documents/{doc}/versions/1/attachment")
        assert dl.status_code == 200 and dl.content == b"%PDF-1.4 hello"
        assert "attachment" in dl.headers["content-disposition"]
        assert c.get(f"/api/documents/{doc}/versions/9/attachment").status_code == 404
        assert c.post("/api/files?filename=e.txt", content=b"").status_code == 409
        assert c.get(f"/api/documents/{doc}/verify").json()["ok"]
