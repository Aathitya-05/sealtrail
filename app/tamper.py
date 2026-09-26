"""DEMO ONLY: play the insider attacker.

These functions bypass the application and edit the database with raw SQL,
exactly like someone with database access would. Then you click "Verify" and
watch the ledger catch it. Disabled when SEALTRAIL_DEMO=0.
"""
import json

from . import files
from . import ledger as L
from . import workflow as W


def _pick(entries, action, default_index=0):
    hits = [e for e in entries if e["action"] == action]
    return hits[default_index] if hits else None


def tamper(conn, doc_id, mode) -> dict:
    entries = L.load_entries(conn, doc_id)
    if not entries:
        raise W.NotFound("Document not found.")
    users = W.get_users(conn)
    name = lambda u: users.get(u, {}).get("name", u)  # noqa: E731

    if mode == "edit_actor":
        t = _pick(entries, "APPROVE") or entries[-1]
        other = next(u for u in users if u not in (t["actor"], entries[0]["actor"]) and users[u]["role"] not in ("AUDITOR", "REQUESTER"))
        conn.execute("UPDATE ledger SET actor=? WHERE doc_id=? AND seq=?", (other, doc_id, t["seq"]))
        return {"seq": t["seq"], "message": f"Changed who approved entry #{t['seq']}: {name(t['actor'])} became {name(other)}. Hashes were NOT recomputed."}

    if mode == "edit_comment":
        t = _pick(entries, "REJECT") or _pick(entries, "APPROVE") or entries[-1]
        conn.execute("UPDATE ledger SET comment=? WHERE doc_id=? AND seq=?", ("Looks fine to me.", doc_id, t["seq"]))
        return {"seq": t["seq"], "message": f"Rewrote the comment on entry #{t['seq']} ({t['action']}) to 'Looks fine to me.'"}

    if mode == "edit_content":
        row = conn.execute("SELECT * FROM versions WHERE doc_id=? ORDER BY version DESC LIMIT 1", (doc_id,)).fetchone()
        c = json.loads(row["content_json"])
        old = c.get("amount", 0)
        c["amount"] = max(1, old // 10)
        conn.execute("UPDATE versions SET content_json=? WHERE doc_id=? AND version=?", (L.canonical(c), doc_id, row["version"]))
        return {"seq": None, "message": f"Changed the amount of v{row['version']} from {old} to {c['amount']} after it was approved. The ledger was not touched."}

    if mode == "replace_file":
        row = conn.execute("SELECT * FROM versions WHERE doc_id=? ORDER BY version DESC LIMIT 1", (doc_id,)).fetchone()
        att = json.loads(row["content_json"]).get("attachment")
        if not att:
            raise W.PolicyError("This document has no attachment to swap. Try DOC-001, or attach a file first.")
        with open(files.path_for(att["sha256"]), "wb") as f:
            f.write(b"SWAPPED AFTER APPROVAL: the quote now says a different price.\n")
        return {"seq": None, "message": f"Overwrote the stored file '{att['filename']}' of v{row['version']} with different bytes. Neither the ledger nor the document text was touched."}

    if mode == "delete_entry":
        t = _pick(entries, "REJECT") or (entries[1] if len(entries) > 2 else entries[-1])
        conn.execute("DELETE FROM ledger WHERE doc_id=? AND seq=?", (doc_id, t["seq"]))
        return {"seq": t["seq"], "message": f"Deleted entry #{t['seq']} ({t['action']} by {name(t['actor'])}) from the database."}

    if mode == "rewrite_chain":
        t = _pick(entries, "APPROVE") or entries[-1]
        other = next(u for u in users if u not in (t["actor"], entries[0]["actor"]) and users[u]["role"] not in ("AUDITOR", "REQUESTER"))
        prev = t["prev_hash"]
        for e in entries:
            if e["seq"] < t["seq"]:
                continue
            if e["seq"] == t["seq"]:
                e["actor"] = other
            e["prev_hash"] = prev
            e["hash"] = L.entry_hash(e)
            prev = e["hash"]
            conn.execute("UPDATE ledger SET actor=?, prev_hash=?, hash=? WHERE doc_id=? AND seq=?",
                         (e["actor"], e["prev_hash"], e["hash"], doc_id, e["seq"]))
        return {"seq": t["seq"], "message": (f"Smart attacker: changed entry #{t['seq']} to {name(other)} AND recomputed every hash "
                                             f"from #{t['seq']} to the end, so the chain is internally consistent.")}

    if mode == "forge_approval":
        state = W.fold(entries, strict=False)
        now_ids = W.waiting_now(state)
        if not now_ids:
            raise W.PolicyError("Nothing to forge: this document is not waiting on anyone. Try a document that is in progress.")
        who = now_ids[0]
        e = L.build_entry(conn, doc_id, state["version"], who, "APPROVE", state["stage_idx"],
                          "Approved (forged)", {"content_hash": state["content_hash"]})
        L.insert_entry(conn, e)  # note: NO anchor written
        return {"seq": e["seq"], "message": f"Injected a perfectly hash-chained APPROVE by {name(who)} (entry #{e['seq']}) directly into the database. It is rule-valid, but it never went through the app."}

    raise W.PolicyError(f"Unknown tamper mode '{mode}'.")
