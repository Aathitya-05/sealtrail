"""Approval rules engine.

The big idea: a document's state is a PURE FUNCTION OF ITS LEDGER.
    state = fold(apply, ledger_entries)
There is no mutable "status" field to tamper with. The same `apply` function
runs twice: once to check an action before it is recorded, and again when an
auditor replays history to check every past action was legal.
"""
import copy
import json
import os

from . import db, files
from . import ledger as L


class PolicyError(Exception):
    """An action that the approval rules do not allow."""


class NotFound(Exception):
    pass


MODES = ("SEQUENTIAL", "PARALLEL")
RULES = ("ALL", "ANY")

TEMPLATES = {
    "standard": {
        "label": "Standard PO: Management (sequential) then Compliance (parallel, all)",
        "stages": [
            {"name": "Management Sign-off", "mode": "SEQUENTIAL", "rule": "ALL", "approvers": ["u_meera", "u_rahul"]},
            {"name": "Compliance Review", "mode": "PARALLEL", "rule": "ALL", "approvers": ["u_sneha", "u_karthik"]},
        ],
    },
    "quick": {
        "label": "Quick approval: one stage, any one of two approvers",
        "stages": [
            {"name": "Quick Approval", "mode": "PARALLEL", "rule": "ANY", "approvers": ["u_meera", "u_rahul"]},
        ],
    },
    "policy": {
        "label": "HR policy: HR + Legal (parallel, all) then Finance Head",
        "stages": [
            {"name": "HR and Legal Review", "mode": "PARALLEL", "rule": "ALL", "approvers": ["u_divya", "u_sneha"]},
            {"name": "Finance Head", "mode": "SEQUENTIAL", "rule": "ALL", "approvers": ["u_rahul"]},
        ],
    },
}


# ------------------------------------------------------------------ validation
def clean_content(c) -> dict:
    if not isinstance(c, dict):
        raise PolicyError("Document content is required.")
    title = str(c.get("title", "")).strip()
    if not title:
        raise PolicyError("Title is required.")
    try:
        amount = int(round(float(c.get("amount", 0) or 0)))
    except (TypeError, ValueError):
        raise PolicyError("Amount must be a number.")
    if amount < 0:
        raise PolicyError("Amount cannot be negative.")
    out = {
        "title": title,
        "description": str(c.get("description", "")).strip(),
        "vendor": str(c.get("vendor", "")).strip(),
        "amount": amount,
    }
    att = c.get("attachment")
    if att:
        if not isinstance(att, dict):
            raise PolicyError("Malformed attachment.")
        name, sha, size = att.get("filename"), att.get("sha256"), att.get("size")
        if not (isinstance(name, str) and name.strip() and isinstance(sha, str) and len(sha) == 64
                and isinstance(size, int) and not isinstance(size, bool) and size >= 0):
            raise PolicyError("Malformed attachment (needs filename, 64-character sha256 and size).")
        if files.state({"sha256": sha}) != "ok":
            raise PolicyError("The attached file is not in the store. Upload it again.")
        out["attachment"] = {"filename": files.clean_name(name) or "attachment", "sha256": sha,
                             "size": os.path.getsize(files.path_for(sha))}  # trust the disk, not the client
    return out


def normalize_stages(stages, owner) -> list:
    if not isinstance(stages, list) or not stages:
        raise PolicyError("At least one approval stage is required.")
    out = []
    for i, s in enumerate(stages):
        if not isinstance(s, dict):
            raise PolicyError("Malformed stage.")
        name = str(s.get("name") or f"Stage {i + 1}").strip()
        mode = str(s.get("mode", "SEQUENTIAL")).upper()
        rule = str(s.get("rule", "ALL")).upper()
        approvers = list(dict.fromkeys(s.get("approvers") or []))
        if mode not in MODES:
            raise PolicyError(f"Stage '{name}': mode must be SEQUENTIAL or PARALLEL.")
        if rule not in RULES:
            raise PolicyError(f"Stage '{name}': rule must be ALL or ANY.")
        if not approvers:
            raise PolicyError(f"Stage '{name}' needs at least one approver.")
        if owner in approvers:
            raise PolicyError("The requester cannot be an approver of their own document (segregation of duties).")
        if mode == "SEQUENTIAL":
            rule = "ALL"  # ordered approvals always need everyone
        out.append({"name": name, "mode": mode, "rule": rule, "approvers": approvers})
    return out


# ------------------------------------------------------------------ the engine
def new_state() -> dict:
    return {
        "version": 0, "status": "NEW", "owner": None, "stages": [], "stage_idx": 0,
        "decisions": {}, "content_hash": None, "stage_since": None, "error": None,
    }


def _stage_complete(stage, dec) -> bool:
    approved = [u for u, d in dec.items() if d["decision"] == "APPROVE"]
    if stage["mode"] == "PARALLEL" and stage["rule"] == "ANY":
        return len(approved) >= 1
    return len(approved) == len(stage["approvers"])


def waiting_now(state) -> list:
    """Who can act on the document right now."""
    if state["status"] != "IN_PROGRESS":
        return []
    stage = state["stages"][state["stage_idx"]]
    dec = state["decisions"].get(str(state["stage_idx"]), {})
    pending = [u for u in stage["approvers"] if u not in dec]
    return pending[:1] if stage["mode"] == "SEQUENTIAL" else pending


def apply(state, e):
    """Apply one ledger entry to the state. Raises PolicyError if it was not legal."""
    a, actor = e["action"], e["actor"]
    p = e.get("payload")
    if not isinstance(p, dict):
        raise PolicyError("entry payload is unreadable")

    if a == "SUBMIT":
        if state["status"] != "NEW" or e["version"] != 1:
            raise PolicyError("a document can only be submitted once (as version 1)")
        owner = p.get("owner")
        if actor != owner:
            raise PolicyError("only the requester can submit")
        if not p.get("content_hash"):
            raise PolicyError("submission is not sealed with a content hash")
        state.update(version=1, status="IN_PROGRESS", owner=owner,
                     stages=normalize_stages(p.get("stages"), owner), stage_idx=0, decisions={},
                     content_hash=p["content_hash"], stage_since=e["ts"])

    elif a == "RESUBMIT":
        if state["status"] != "REJECTED":
            raise PolicyError("only a rejected document can be resubmitted")
        if actor != state["owner"]:
            raise PolicyError("only the requester can resubmit")
        if e["version"] != state["version"] + 1:
            raise PolicyError("resubmission must create the next version number")
        if not p.get("content_hash") or p["content_hash"] == state["content_hash"]:
            raise PolicyError("a resubmission must carry revised content")
        state.update(version=e["version"], status="IN_PROGRESS",
                     stages=normalize_stages(p.get("stages"), state["owner"]), stage_idx=0, decisions={},
                     content_hash=p["content_hash"], stage_since=e["ts"])

    elif a in ("APPROVE", "REJECT"):
        if state["status"] != "IN_PROGRESS":
            raise PolicyError(f"the document is {state['status']}; no decisions can be recorded")
        if e["version"] != state["version"]:
            raise PolicyError("decision refers to a different version")
        idx = state["stage_idx"]
        if e["stage_idx"] != idx:
            raise PolicyError("decision refers to a stage that is not the current stage")
        stage = state["stages"][idx]
        if actor == state["owner"]:
            raise PolicyError("the requester cannot decide on their own document")
        if actor not in stage["approvers"]:
            raise PolicyError(f"{actor} is not an approver of stage '{stage['name']}'")
        dec = state["decisions"].setdefault(str(idx), {})
        if actor in dec:
            raise PolicyError(f"{actor} has already decided in this stage")
        if stage["mode"] == "SEQUENTIAL":
            expected = next(u for u in stage["approvers"] if u not in dec)
            if actor != expected:
                raise PolicyError(f"sequential stage: it is {expected}'s turn first")
        if a == "APPROVE" and p.get("content_hash") != state["content_hash"]:
            raise PolicyError("approval is not bound to the current content version")
        if a == "REJECT" and not (e.get("comment") or "").strip():
            raise PolicyError("a rejection must include a comment")
        dec[actor] = {"decision": a, "seq": e.get("seq"), "ts": e["ts"], "comment": e.get("comment", "")}
        if a == "REJECT":
            state["status"] = "REJECTED"
            state["stage_since"] = e["ts"]
        elif _stage_complete(stage, dec):
            state["stage_idx"] = idx + 1
            state["stage_since"] = e["ts"]
            if state["stage_idx"] >= len(state["stages"]):
                state["status"] = "APPROVED"

    elif a == "COMMENT":
        if state["status"] == "NEW":
            raise PolicyError("nothing to comment on yet")
        if not (e.get("comment") or "").strip():
            raise PolicyError("a comment cannot be empty")
    else:
        raise PolicyError(f"unknown action {a}")
    return state


def fold(entries, upto=None, strict=True) -> dict:
    """Replay history. strict=False keeps the last trustworthy state and records where replay broke."""
    state = new_state()
    for e in (entries if upto is None else entries[:upto]):
        try:
            apply(state, e)
        except Exception as ex:  # noqa: BLE001
            if strict:
                raise PolicyError(str(ex)) if not isinstance(ex, PolicyError) else ex
            state["error"] = {"seq": e.get("seq"), "message": str(ex)}
            break
    return state


# ------------------------------------------------------------------ view helpers
def get_users(conn) -> dict:
    return {r["id"]: dict(r) for r in conn.execute("SELECT * FROM users ORDER BY rowid").fetchall()}


def pipeline(state, users) -> list:
    out = []
    now_ids = waiting_now(state)
    for i, s in enumerate(state["stages"]):
        dec = state["decisions"].get(str(i), {})
        if state["status"] == "APPROVED" or i < state["stage_idx"]:
            st = "DONE"
        elif i == state["stage_idx"]:
            st = "REJECTED" if state["status"] == "REJECTED" else "ACTIVE"
        else:
            st = "QUEUED"
        appr = []
        for u in s["approvers"]:
            d = dec.get(u)
            if d:
                us = "APPROVED" if d["decision"] == "APPROVE" else "REJECTED"
            elif st == "ACTIVE":
                us = "WAITING" if u in now_ids else "QUEUED"
            elif st == "DONE":
                us = "NOT_NEEDED"
            elif st == "REJECTED":
                us = "NOT_NEEDED"
            else:
                us = "QUEUED"
            usr = users.get(u, {"name": u, "title": ""})
            appr.append({"id": u, "name": usr["name"], "title": usr["title"], "state": us,
                         "ts": d["ts"] if d else None, "comment": d["comment"] if d else ""})
        out.append({"index": i, "name": s["name"], "mode": s["mode"], "rule": s["rule"],
                    "status": st, "approvers": appr})
    return out


def explain(state, users) -> dict:
    """The 'Why is it stuck?' panel."""
    def nm(u):
        x = users.get(u)
        return f"{x['name']} ({x['title']})" if x else u

    n = len(state["stages"])
    if state["status"] == "APPROVED":
        return {"tone": "ok", "headline": "Fully approved",
                "detail": f"All {n} stage{'s' if n != 1 else ''} were satisfied on version v{state['version']}. Nothing is pending.",
                "waiting_on": [], "since": state["stage_since"]}
    if state["status"] == "REJECTED":
        idx = state["stage_idx"]
        dec = state["decisions"].get(str(idx), {})
        who, d = next(((u, d) for u, d in dec.items() if d["decision"] == "REJECT"), (None, {"comment": ""}))
        return {"tone": "bad", "headline": f"Rejected by {users.get(who, {}).get('name', who)} at stage {idx + 1}",
                "detail": f"Reason: “{d['comment']}”. Only the requester ({nm(state['owner'])}) can resubmit a revised "
                          f"version (v{state['version'] + 1}). The rejected version and its history stay on record.",
                "waiting_on": [state["owner"]], "since": state["stage_since"]}
    if state["status"] == "IN_PROGRESS":
        idx = state["stage_idx"]
        s = state["stages"][idx]
        dec = state["decisions"].get(str(idx), {})
        done = [u for u, d in dec.items() if d["decision"] == "APPROVE"]
        pend = [u for u in s["approvers"] if u not in dec]
        head = f"Stage {idx + 1} of {n}, “{s['name']}”"
        if s["mode"] == "SEQUENTIAL":
            later = pend[1:]
            detail = (f"{head} is SEQUENTIAL: approvers must act in order. "
                      + (f"{', '.join(users.get(u, {}).get('name', u) for u in done)} already approved. " if done else "")
                      + f"It is {users.get(pend[0], {}).get('name', pend[0])}'s turn."
                      + (f" {', '.join(users.get(u, {}).get('name', u) for u in later)} cannot act until then." if later else ""))
            return {"tone": "warn", "headline": f"Waiting on {nm(pend[0])}", "detail": detail,
                    "waiting_on": pend[:1], "since": state["stage_since"]}
        if s["rule"] == "ANY":
            return {"tone": "warn",
                    "headline": "Waiting on any one of " + ", ".join(users.get(u, {}).get("name", u) for u in pend),
                    "detail": f"{head} is PARALLEL and needs ANY one approval. The first approval completes the stage and releases the others.",
                    "waiting_on": pend, "since": state["stage_since"]}
        return {"tone": "warn", "headline": "Waiting on " + ", ".join(users.get(u, {}).get("name", u) for u in pend),
                "detail": f"{head} is PARALLEL and needs ALL approvers: {len(done)} of {len(s['approvers'])} approved so far. "
                          f"Everyone listed can act now in any order; the stage completes when the last one approves.",
                "waiting_on": pend, "since": state["stage_since"]}
    return {"tone": "info", "headline": "Not submitted", "detail": "", "waiting_on": [], "since": None}


def _load_versions(conn, doc_id):
    out = []
    for r in conn.execute("SELECT * FROM versions WHERE doc_id=? ORDER BY version", (doc_id,)).fetchall():
        try:
            content = json.loads(r["content_json"])
        except Exception:
            content = {}
        out.append({"version": r["version"], "content": content,
                    "content_hash": r["content_hash"], "created_at": r["created_at"]})
    return out


def document_view(conn, doc_id, at=None) -> dict:
    entries = L.load_entries(conn, doc_id)
    if not entries:
        raise NotFound(f"Document {doc_id} not found.")
    total = len(entries)
    upto = total if at is None else max(0, min(int(at), total))
    state = fold(entries, upto, strict=False)
    users = get_users(conn)
    versions = _load_versions(conn, doc_id)
    cur = next((v for v in versions if v["version"] == state["version"]), None)
    view_entries = []
    for pos, e in enumerate(entries, start=1):
        u = users.get(e["actor"], {"name": e["actor"], "title": ""})
        payload = {k: v for k, v in (e.get("payload") or {}).items() if k != "stages"}
        view_entries.append({
            "seq": e["seq"], "ts": e["ts"], "version": e["version"], "actor": e["actor"],
            "actor_name": u["name"], "actor_title": u["title"], "action": e["action"],
            "stage_idx": e["stage_idx"], "comment": e["comment"], "payload": payload,
            "prev_hash": e["prev_hash"], "hash": e["hash"], "future": pos > upto,
        })
    owner = users.get(state["owner"]) if state["owner"] else None
    return {
        "id": doc_id, "total": total, "at": upto,
        "state": {"version": state["version"], "status": state["status"], "stage_idx": state["stage_idx"],
                  "stage_count": len(state["stages"]), "error": state["error"],
                  "stage_since": state["stage_since"], "content_hash": state["content_hash"]},
        "content": cur["content"] if cur else None,
        "versions": versions,
        "owner": owner,
        "pipeline": pipeline(state, users),
        "why": explain(state, users),
        "waiting_now": waiting_now(state),
        "entries": view_entries,
    }


def list_documents(conn) -> list:
    users = get_users(conn)
    out = []
    for r in conn.execute("SELECT id FROM documents ORDER BY id").fetchall():
        entries = L.load_entries(conn, r["id"])
        if not entries:
            continue
        state = fold(entries, strict=False)
        vers = _load_versions(conn, r["id"])
        cur = next((v for v in vers if v["version"] == state["version"]), None) or (vers[-1] if vers else None)
        c = cur["content"] if cur else {}
        stage = state["stages"][state["stage_idx"]] if state["status"] == "IN_PROGRESS" else None
        out.append({
            "id": r["id"], "title": c.get("title", "(unreadable)"), "amount": c.get("amount", 0),
            "owner": users.get(state["owner"], {}).get("name", state["owner"]),
            "version": state["version"], "status": state["status"],
            "stage": (stage["name"] if stage else None), "stage_no": state["stage_idx"] + 1,
            "stage_count": len(state["stages"]),
            "waiting_on": [users.get(u, {}).get("name", u) for u in waiting_now(state)],
            "updated": entries[-1]["ts"], "entries": len(entries), "degraded": bool(state["error"]),
        })
    return out


# ------------------------------------------------------------------ actions
def _user_or_fail(conn, uid):
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not row:
        raise PolicyError(f"Unknown user '{uid}'.")
    return row


def create_document(conn, owner, content, stages, ts=None) -> str:
    with db.WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _user_or_fail(conn, owner)
            c = clean_content(content)
            st = normalize_stages(stages, owner)
            known = {r["id"] for r in conn.execute("SELECT id FROM users").fetchall()}
            unknown = [a for s in st for a in s["approvers"] if a not in known]
            if unknown:
                raise PolicyError(f"Unknown approver(s): {', '.join(unknown)}")
            n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] + 1
            doc_id = f"DOC-{n:03d}"
            while conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
                n += 1
                doc_id = f"DOC-{n:03d}"
            when = ts or L.now_iso()
            ch = L.content_hash(c)
            conn.execute("INSERT INTO documents(id, created_at) VALUES(?,?)", (doc_id, when))
            conn.execute("INSERT INTO versions VALUES(?,?,?,?,?)", (doc_id, 1, L.canonical(c), ch, when))
            L.append(conn, doc_id, 1, owner, "SUBMIT", 0, "Submitted for approval",
                     {"owner": owner, "stages": st, "content_hash": ch}, ts=when)
            conn.execute("COMMIT")
            return doc_id
        except Exception:
            conn.execute("ROLLBACK")
            raise


def perform(conn, doc_id, actor, action, comment="", content=None, ts=None) -> dict:
    """Validate an action against the rules, then chain + anchor it. Returns the new ledger entry."""
    action = str(action).upper()
    comment = (comment or "").strip()
    with db.WRITE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            _user_or_fail(conn, actor)
            entries = L.load_entries(conn, doc_id)
            if not entries:
                raise NotFound(f"Document {doc_id} not found.")
            state = fold(entries)  # strict: refuse to build on a history that is already illegal
            when = ts or L.now_iso()
            cand = {"seq": entries[-1]["seq"] + 1, "ts": when, "actor": actor, "action": action,
                    "comment": comment, "version": state["version"], "stage_idx": state["stage_idx"], "payload": {}}
            if action in ("APPROVE", "REJECT"):
                cand["payload"] = {"content_hash": state["content_hash"]}
            elif action == "COMMENT":
                pass
            elif action == "RESUBMIT":
                c = clean_content(content)
                prev = next(v["content"] for v in _load_versions(conn, doc_id) if v["version"] == state["version"])
                changed = sorted(k for k in set(c) | set(prev) if c.get(k) != prev.get(k))
                if not changed:
                    raise PolicyError("A resubmission must change something. Revise the document to address the rejection.")
                ch = L.content_hash(c)
                cand.update(version=state["version"] + 1, stage_idx=0,
                            payload={"owner": state["owner"], "stages": state["stages"],
                                     "content_hash": ch, "changed": changed})
            else:
                raise PolicyError(f"Unknown action '{action}'.")
            apply(copy.deepcopy(state), cand)  # dry-run: raises PolicyError if not allowed
            if action == "RESUBMIT":
                conn.execute("INSERT INTO versions VALUES(?,?,?,?,?)",
                             (doc_id, cand["version"], L.canonical(c), cand["payload"]["content_hash"], when))
            e = L.append(conn, doc_id, cand["version"], actor, action, cand["stage_idx"],
                         comment, cand["payload"], ts=when)
            conn.execute("COMMIT")
            return e
        except Exception:
            conn.execute("ROLLBACK")
            raise
