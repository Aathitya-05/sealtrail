"""Checkpoint: a saved anchor head proves later that history was not rewritten."""
from fastapi.testclient import TestClient

from app import db
from app import ledger as L
from app import workflow as W
from app.main import app


def test_checkpoint_matches_and_survives_new_entries(conn):
    cp = L.checkpoint()
    assert cp["log_ok"] and len(cp["anchor_head"]) == 64 and cp["entries"] > 0
    W.perform(conn, "DOC-004", "u_meera", "APPROVE", "ok")
    r = L.check_checkpoint(cp["anchor_head"])
    assert r["found"] and r["position"] == cp["entries"] and r["total"] == cp["entries"] + 1


def test_checkpoint_detects_history_rewritten_after_saving(conn):
    cp = L.checkpoint()
    lines = L.read_anchor_lines()[:-2]  # attacker truncates the log and re-chains it consistently
    import json
    open(db.ANCHOR_PATH, "w", encoding="utf-8").write("".join(json.dumps(x) + "\n" for x in lines))
    assert L.anchor_log_ok()
    r = L.check_checkpoint(cp["anchor_head"])
    assert not r["found"] and "NOT FOUND" in r["message"]


def test_checkpoint_fails_when_log_itself_is_broken(conn):
    cp = L.checkpoint()
    lines = open(db.ANCHOR_PATH, encoding="utf-8").read().splitlines()
    lines[0] = lines[0].replace('"hash": "', '"hash": "f', 1)
    open(db.ANCHOR_PATH, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    r = L.check_checkpoint(cp["anchor_head"])
    assert not r["found"] and not r["log_ok"]


def test_checkpoint_endpoints(conn):
    with TestClient(app) as c:
        cp = c.get("/api/checkpoint").json()
        r = c.post("/api/checkpoint/check", json={"anchor": cp["anchor_head"]}).json()
        assert r["found"]
        assert not c.post("/api/checkpoint/check", json={"anchor": "b" * 64}).json()["found"]
