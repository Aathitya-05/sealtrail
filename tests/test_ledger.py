"""The tamper-evidence claims: every attack must be caught, and named precisely."""
import json
import subprocess
import sys
import os

import pytest

from app import ledger as L
from app import tamper
from app import workflow as W


def codes(res):
    return {b["code"] for b in res["breaks"]}


def test_clean_ledgers_verify(conn):
    for d in ("DOC-001", "DOC-002", "DOC-003", "DOC-004"):
        r = L.verify(conn, d)
        assert r["ok"], r
        assert r["anchored"] == r["entries"]
        assert all(r["checks"].values())


def test_chain_links_are_real(conn):
    es = L.load_entries(conn, "DOC-001")
    assert es[0]["prev_hash"] == L.GENESIS
    for a, b in zip(es, es[1:]):
        assert b["prev_hash"] == a["hash"]


def test_edit_actor_is_caught_at_that_entry(conn):
    res = tamper.tamper(conn, "DOC-001", "edit_actor")
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and r["first_break"] == res["seq"]
    assert "ENTRY_EDITED" in codes(r)
    assert r["status"][res["seq"]] == "bad"
    later = [s for s in r["status"] if s > res["seq"]]
    assert later and all(r["status"][s] in ("bad", "untrusted") for s in later)


def test_edit_comment_is_caught(conn):
    res = tamper.tamper(conn, "DOC-003", "edit_comment")  # erases the rejection reason
    r = L.verify(conn, "DOC-003")
    assert not r["ok"] and r["first_break"] == res["seq"]


def test_deleting_a_middle_entry_is_caught(conn):
    res = tamper.tamper(conn, "DOC-003", "delete_entry")
    r = L.verify(conn, "DOC-003")
    assert not r["ok"]
    assert {"ENTRY_MISSING", "LINK_BROKEN"} <= codes(r)
    assert r["first_break"] == res["seq"]


def test_deleting_the_last_entry_is_caught_by_anchors(conn):
    conn.execute("DELETE FROM ledger WHERE doc_id='DOC-001' AND seq=5")
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and "ANCHOR_MISSING_ENTRY" in codes(r)  # the chain alone cannot see this


def test_editing_document_content_is_caught(conn):
    tamper.tamper(conn, "DOC-001", "edit_content")
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and "CONTENT_EDITED" in codes(r)
    assert r["first_break"] == 1  # the seal was made at the SUBMIT entry


def test_smart_attacker_who_rewrites_whole_chain_is_caught_by_anchors(conn):
    res = tamper.tamper(conn, "DOC-001", "rewrite_chain")
    r = L.verify(conn, "DOC-001")
    # chain is internally consistent...
    assert r["checks"]["chain"] and r["checks"]["entries"]
    # ...but the external anchors disagree
    assert not r["ok"] and "ANCHOR_MISMATCH" in codes(r)
    assert r["first_break"] == res["seq"]


def test_forged_but_rule_valid_approval_is_caught_as_unanchored(conn):
    res = tamper.tamper(conn, "DOC-004", "forge_approval")
    r = L.verify(conn, "DOC-004")
    assert not r["ok"] and "UNANCHORED" in codes(r)
    assert r["first_break"] == res["seq"]


def test_rules_replay_catches_illegal_history(conn):
    # An attacker who also rewrites anchors is stopped by the rules: Rahul cannot approve before Meera.
    es = L.load_entries(conn, "DOC-004")  # quick template: Meera/Rahul in parallel, so use DOC-002 (sequential)
    es = L.load_entries(conn, "DOC-002")
    state = W.new_state()
    W.apply(state, es[0])
    bad = dict(es[2])  # Rahul's approval...
    bad["stage_idx"] = 0
    with pytest.raises(W.PolicyError, match="turn first"):
        W.apply(state, bad)  # ...replayed without Meera's approval first


def test_deleting_a_whole_document_is_caught_by_audit_all(conn):
    from fastapi.testclient import TestClient
    from app.main import app
    conn.execute("DELETE FROM ledger WHERE doc_id='DOC-004'")
    conn.execute("DELETE FROM versions WHERE doc_id='DOC-004'")
    conn.execute("DELETE FROM documents WHERE id='DOC-004'")
    with TestClient(app) as client:
        r = client.get("/api/verify").json()
    bad = {d["doc_id"] for d in r["docs"] if not d["ok"]}
    assert not r["ok"] and bad == {"DOC-004"}


def test_tampered_anchor_log_is_reported(conn):
    from app import db
    with open(db.ANCHOR_PATH, "r+", encoding="utf-8") as f:
        lines = f.read().splitlines()
        rec = json.loads(lines[0])
        rec["hash"] = "f" * 64
        lines[0] = json.dumps(rec)
        f.seek(0)
        f.write("\n".join(lines) + "\n")
        f.truncate()
    r = L.verify(conn, "DOC-001")
    assert not r["ok"] and "ANCHOR_LOG_TAMPERED" in codes(r)


def test_view_survives_tampering_and_reports_where_replay_broke(conn):
    tamper.tamper(conn, "DOC-001", "edit_actor")
    v = W.document_view(conn, "DOC-001")  # must not crash
    assert v["state"]["error"] is not None


def test_offline_bundle_verifier(conn, tmp_path):
    conn_bundle = {"format": "sealtrail-bundle/1", "doc_id": "DOC-001",
                   "versions": [{"version": v["version"], "content": v["content"], "content_hash": v["content_hash"]}
                                for v in W._load_versions(conn, "DOC-001")],
                   "ledger": L.load_entries(conn, "DOC-001"),
                   "anchors": [a for a in L.read_anchor_lines() if a["doc_id"] == "DOC-001"]}
    p = tmp_path / "bundle.json"
    p.write_text(json.dumps(conn_bundle))
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "verify_bundle.py")
    ok = subprocess.run([sys.executable, tool, str(p)], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    conn_bundle["ledger"][1]["actor"] = "u_rahul"
    p.write_text(json.dumps(conn_bundle))
    bad = subprocess.run([sys.executable, tool, str(p)], capture_output=True, text=True)
    assert bad.returncode == 1 and "#2" in bad.stdout
