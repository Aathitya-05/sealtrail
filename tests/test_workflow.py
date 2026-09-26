"""Approval rules: sequential, parallel ALL/ANY, reject, comment, resubmit."""
import threading

import pytest

from app import ledger as L
from app import workflow as W

CONTENT = {"title": "Test PO", "vendor": "ACME", "amount": 1000, "description": "x"}
STD = W.TEMPLATES["standard"]["stages"]


def make(conn, stages=STD, owner="u_aarav"):
    return W.create_document(conn, owner, CONTENT, stages)


def state(conn, doc):
    return W.fold(L.load_entries(conn, doc))


def test_sequential_enforces_order(blank):
    d = make(blank)
    with pytest.raises(W.PolicyError, match="turn first"):
        W.perform(blank, d, "u_rahul", "APPROVE")  # Rahul tries before Meera
    W.perform(blank, d, "u_meera", "APPROVE")
    W.perform(blank, d, "u_rahul", "APPROVE")
    assert state(blank, d)["stage_idx"] == 1


def test_parallel_all_needs_everyone_any_order(blank):
    d = make(blank)
    W.perform(blank, d, "u_meera", "APPROVE")
    W.perform(blank, d, "u_rahul", "APPROVE")
    W.perform(blank, d, "u_karthik", "APPROVE")  # order does not matter in parallel
    assert state(blank, d)["status"] == "IN_PROGRESS"
    W.perform(blank, d, "u_sneha", "APPROVE")
    assert state(blank, d)["status"] == "APPROVED"


def test_parallel_any_first_approval_completes(blank):
    d = make(blank, W.TEMPLATES["quick"]["stages"])
    W.perform(blank, d, "u_rahul", "APPROVE")
    assert state(blank, d)["status"] == "APPROVED"
    with pytest.raises(W.PolicyError):  # the other approver is released, cannot act
        W.perform(blank, d, "u_meera", "APPROVE")


def test_only_listed_approvers_act(blank):
    d = make(blank)
    with pytest.raises(W.PolicyError, match="not an approver"):
        W.perform(blank, d, "u_divya", "APPROVE")


def test_requester_cannot_approve_own_document(blank):
    with pytest.raises(W.PolicyError, match="segregation"):
        make(blank, [{"name": "S", "mode": "PARALLEL", "rule": "ALL", "approvers": ["u_aarav", "u_meera"]}])


def test_cannot_approve_twice(blank):
    d = make(blank)
    W.perform(blank, d, "u_meera", "APPROVE")
    with pytest.raises(W.PolicyError):
        W.perform(blank, d, "u_meera", "APPROVE")


def test_reject_needs_comment_and_stops_everything(blank):
    d = make(blank)
    with pytest.raises(W.PolicyError, match="comment"):
        W.perform(blank, d, "u_meera", "REJECT", "")
    W.perform(blank, d, "u_meera", "REJECT", "Wrong vendor")
    s = state(blank, d)
    assert s["status"] == "REJECTED"
    with pytest.raises(W.PolicyError):
        W.perform(blank, d, "u_rahul", "APPROVE")


def test_parallel_reject_beats_partial_approvals(blank):
    d = make(blank)
    W.perform(blank, d, "u_meera", "APPROVE")
    W.perform(blank, d, "u_rahul", "APPROVE")
    W.perform(blank, d, "u_sneha", "APPROVE")
    W.perform(blank, d, "u_karthik", "REJECT", "Quote expired")
    assert state(blank, d)["status"] == "REJECTED"


def test_comment_allowed_for_anyone_but_not_empty(blank):
    d = make(blank)
    W.perform(blank, d, "u_divya", "COMMENT", "FYI, HR has budget concerns")
    with pytest.raises(W.PolicyError):
        W.perform(blank, d, "u_divya", "COMMENT", "  ")
    assert state(blank, d)["status"] == "IN_PROGRESS"


def test_resubmit_creates_v2_restarts_and_keeps_history(blank):
    d = make(blank)
    W.perform(blank, d, "u_meera", "APPROVE")
    W.perform(blank, d, "u_rahul", "REJECT", "Add cost centre")
    before = len(L.load_entries(blank, d))
    with pytest.raises(W.PolicyError, match="only the requester"):
        W.perform(blank, d, "u_meera", "RESUBMIT", content={**CONTENT, "amount": 900})
    with pytest.raises(W.PolicyError, match="change something"):
        W.perform(blank, d, "u_aarav", "RESUBMIT", content=CONTENT)  # nothing revised
    W.perform(blank, d, "u_aarav", "RESUBMIT", "Fixed", content={**CONTENT, "amount": 900})
    s = state(blank, d)
    assert (s["version"], s["status"], s["stage_idx"]) == (2, "IN_PROGRESS", 0)
    assert s["decisions"] == {}  # approvals from v1 do not carry over
    assert len(L.load_entries(blank, d)) == before + 1  # nothing was removed
    assert blank.execute("SELECT COUNT(*) FROM versions WHERE doc_id=?", (d,)).fetchone()[0] == 2
    W.perform(blank, d, "u_meera", "APPROVE")  # v2 needs fresh approval from Meera


def test_resubmit_only_when_rejected(blank):
    d = make(blank)
    with pytest.raises(W.PolicyError, match="rejected"):
        W.perform(blank, d, "u_aarav", "RESUBMIT", content={**CONTENT, "amount": 1})


def test_status_is_not_stored_anywhere(blank):
    cols = {r["name"] for t in ("documents", "versions", "ledger")
            for r in blank.execute(f"PRAGMA table_info({t})").fetchall()}
    assert "status" not in cols


def test_concurrent_approvals_only_one_wins(blank):
    d = make(blank, [{"name": "S", "mode": "PARALLEL", "rule": "ANY", "approvers": ["u_meera", "u_rahul"]}])
    results = []

    def go(u):
        c = W.db.connect()
        try:
            W.perform(c, d, u, "APPROVE")
            results.append("ok")
        except W.PolicyError:
            results.append("blocked")
        finally:
            c.close()

    ts = [threading.Thread(target=go, args=(u,)) for u in ("u_meera", "u_rahul")]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(results) == ["blocked", "ok"]
    assert L.verify(blank, d)["ok"]


def test_time_travel_replay(blank):
    d = make(blank)
    W.perform(blank, d, "u_meera", "APPROVE")
    W.perform(blank, d, "u_rahul", "APPROVE")
    v = W.document_view(blank, d, at=1)
    assert v["state"]["stage_idx"] == 0 and v["waiting_now"] == ["u_meera"]
    v = W.document_view(blank, d, at=3)
    assert v["state"]["stage_idx"] == 1
    assert [e["future"] for e in v["entries"]] == [False, False, False]


def test_why_is_it_stuck(conn):
    v = W.document_view(conn, "DOC-002")
    assert v["why"]["waiting_on"] == ["u_karthik"]
    assert "Karthik" in v["why"]["headline"]
