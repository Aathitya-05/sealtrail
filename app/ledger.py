"""The tamper-evident ledger: hash chain + content seals + external anchors.

Three independent defences (each one catches what the previous cannot):

1. HASH CHAIN   every entry stores SHA-256(its own fields + previous entry's hash).
                Edit or delete anything in the middle and the links break.
2. CONTENT SEAL the SUBMIT/RESUBMIT entry stores the SHA-256 of the document
                content. Every APPROVE repeats that hash, so an approval is
                bound to the exact text/amount that was approved.
3. ANCHORS      after each entry, its hash is also appended to a separate
                append-only log that is itself hash-chained. An attacker who
                rewrites the whole DB chain (recomputing every hash) still
                cannot make it match the anchors. In production this log
                would live somewhere else (email, WORM storage, a notary).
"""
import datetime as dt
import hashlib
import json
import os

from . import db, files

GENESIS = "0" * 64
HASH_FIELDS = ("doc_id", "seq", "ts", "version", "actor", "action",
               "stage_idx", "comment", "payload", "prev_hash")


# --------------------------------------------------------------------- hashing
def canonical(obj) -> str:
    """Deterministic JSON: same data always gives the same bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_hash(content: dict) -> str:
    return sha256(canonical(content))


def entry_hash(e: dict) -> str:
    return sha256(canonical({k: e[k] for k in HASH_FIELDS}))


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds")


# --------------------------------------------------------------------- storage
def row_to_entry(row) -> dict:
    e = dict(row)
    raw = e.pop("payload_json", None)
    try:
        e["payload"] = json.loads(raw)
    except Exception:
        e["payload"] = None
        e["payload_bad"] = True
    return e


def load_entries(conn, doc_id: str) -> list:
    rows = conn.execute("SELECT * FROM ledger WHERE doc_id=? ORDER BY seq", (doc_id,)).fetchall()
    return [row_to_entry(r) for r in rows]


def build_entry(conn, doc_id, version, actor, action, stage_idx, comment, payload, ts=None) -> dict:
    last = conn.execute(
        "SELECT seq, hash FROM ledger WHERE doc_id=? ORDER BY seq DESC LIMIT 1", (doc_id,)
    ).fetchone()
    e = {
        "doc_id": doc_id,
        "seq": (last["seq"] + 1) if last else 1,
        "ts": ts or now_iso(),
        "version": version,
        "actor": actor,
        "action": action,
        "stage_idx": stage_idx,
        "comment": comment or "",
        "payload": payload or {},
        "prev_hash": last["hash"] if last else GENESIS,
    }
    e["hash"] = entry_hash(e)
    return e


def insert_entry(conn, e: dict):
    conn.execute(
        "INSERT INTO ledger(doc_id,seq,ts,version,actor,action,stage_idx,comment,payload_json,prev_hash,hash)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (e["doc_id"], e["seq"], e["ts"], e["version"], e["actor"], e["action"], e["stage_idx"],
         e["comment"], canonical(e["payload"]), e["prev_hash"], e["hash"]),
    )


def append(conn, doc_id, version, actor, action, stage_idx, comment, payload, ts=None) -> dict:
    """The ONLY legitimate way to add history: chain it, store it, anchor it."""
    e = build_entry(conn, doc_id, version, actor, action, stage_idx, comment, payload, ts)
    insert_entry(conn, e)
    write_anchor(e)
    return e


# --------------------------------------------------------------------- anchors
def read_anchor_lines() -> list:
    if not os.path.exists(db.ANCHOR_PATH):
        return []
    out = []
    with open(db.ANCHOR_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                out.append({"_corrupt": True})
    return out


def anchor_digest(prev, doc_id, seq, h) -> str:
    return sha256(f"{prev}|{doc_id}|{seq}|{h}")


def write_anchor(e: dict):
    lines = read_anchor_lines()
    prev = lines[-1].get("anchor", GENESIS) if lines else GENESIS
    rec = {"ts": e["ts"], "doc_id": e["doc_id"], "seq": e["seq"], "hash": e["hash"], "prev": prev}
    rec["anchor"] = anchor_digest(prev, e["doc_id"], e["seq"], e["hash"])
    os.makedirs(os.path.dirname(os.path.abspath(db.ANCHOR_PATH)), exist_ok=True)
    with open(db.ANCHOR_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())


def anchor_log_ok(lines=None) -> bool:
    """Is the anchor log itself an unbroken chain?"""
    lines = read_anchor_lines() if lines is None else lines
    prev = GENESIS
    for rec in lines:
        if rec.get("_corrupt") or rec.get("prev") != prev:
            return False
        if rec.get("anchor") != anchor_digest(prev, rec.get("doc_id"), rec.get("seq"), rec.get("hash")):
            return False
        prev = rec["anchor"]
    return True


# ---------------------------------------------------------------- verification
CHECK_OF = {
    "ENTRY_MISSING": "sequence", "LINK_BROKEN": "chain", "ENTRY_EDITED": "entries",
    "PAYLOAD_UNREADABLE": "entries", "CONTENT_EDITED": "content", "VERSION_MISSING": "content",
    "FILE_MISSING": "content", "FILE_EDITED": "content",
    "ANCHOR_MISMATCH": "anchors", "UNANCHORED": "anchors", "ANCHOR_LOG_TAMPERED": "anchors",
    "ANCHOR_MISSING_ENTRY": "anchors", "RULE_VIOLATION": "rules",
}


def verify(conn, doc_id: str, anchor_lines=None) -> dict:
    """Audit one document. Returns exactly which entry is broken and why."""
    from . import workflow  # local import: workflow imports this module

    entries = load_entries(conn, doc_id)
    by_seq = {e["seq"]: e for e in entries}
    breaks = []
    checks = {"sequence": True, "chain": True, "entries": True,
              "content": True, "anchors": True, "rules": True}

    def brk(seq, code, msg):
        if any(b["seq"] == seq and b["code"] == code for b in breaks):
            return
        breaks.append({"seq": seq, "code": code, "message": msg})
        checks[CHECK_OF[code]] = False

    # 1. sequence numbers must be 1..n with no gaps
    expected = 1
    for e in entries:
        while expected < e["seq"]:
            brk(expected, "ENTRY_MISSING", f"Entry #{expected} is missing. It was deleted after being recorded.")
            expected += 1
        expected = e["seq"] + 1

    # 2. hash chain + 3. each entry's own hash
    prev = GENESIS
    for e in entries:
        if e.get("payload_bad"):
            brk(e["seq"], "PAYLOAD_UNREADABLE", f"Entry #{e['seq']} has a corrupted payload.")
        if e["prev_hash"] != prev:
            brk(e["seq"], "LINK_BROKEN",
                f"Entry #{e['seq']} no longer points to the entry before it. The chain was cut here.")
        if entry_hash(e) != e["hash"]:
            brk(e["seq"], "ENTRY_EDITED",
                f"Entry #{e['seq']} ({e['action']}) no longer matches its own hash. Its contents were edited after sealing.")
        prev = e["hash"]

    # 4. content seals: stored document text must equal the hash sealed in the ledger
    vrows = conn.execute("SELECT * FROM versions WHERE doc_id=?", (doc_id,)).fetchall()
    vmap = {r["version"]: r for r in vrows}
    for e in entries:
        if e["action"] in ("SUBMIT", "RESUBMIT") and isinstance(e.get("payload"), dict):
            sealed = e["payload"].get("content_hash")
            row = vmap.get(e["version"])
            if row is None:
                brk(e["seq"], "VERSION_MISSING", f"Version {e['version']} content is missing from the database.")
                continue
            try:
                actual = content_hash(json.loads(row["content_json"]))
            except Exception:
                actual = None
            if actual != sealed:
                brk(e["seq"], "CONTENT_EDITED",
                    f"The content of version {e['version']} was changed after it was sealed at entry #{e['seq']}. "
                    f"Approvals no longer refer to what is on screen.")

    # 4b. attached files: the stored bytes must still hash to the sealed sha256
    for e in entries:
        if e["action"] in ("SUBMIT", "RESUBMIT"):
            row = vmap.get(e["version"])
            try:
                att = json.loads(row["content_json"]).get("attachment") if row else None
            except Exception:
                att = None
            if att:
                st = files.state(att)
                if st == "missing":
                    brk(e["seq"], "FILE_MISSING",
                        f"The file attached to version {e['version']} ({att.get('filename')}) is missing from storage. "
                        f"It was sealed at entry #{e['seq']}.")
                elif st == "edited":
                    brk(e["seq"], "FILE_EDITED",
                        f"The file attached to version {e['version']} ({att.get('filename')}) was replaced after it was "
                        f"sealed at entry #{e['seq']}. Approvals no longer refer to the file that was approved.")

    # 5. external anchors
    lines = read_anchor_lines() if anchor_lines is None else anchor_lines
    if not anchor_log_ok(lines):
        brk(None, "ANCHOR_LOG_TAMPERED", "The external anchor log itself has been tampered with.")
    anchors = {}
    for rec in lines:
        if rec.get("doc_id") == doc_id:
            anchors[rec.get("seq")] = rec.get("hash")
    for seq, h in anchors.items():
        e = by_seq.get(seq)
        if e is None:
            brk(seq, "ANCHOR_MISSING_ENTRY",
                f"Entry #{seq} was sealed in the external anchor log but is gone from the database.")
        elif e["hash"] != h:
            brk(seq, "ANCHOR_MISMATCH",
                f"Entry #{seq} differs from the copy sealed in the external anchor log. "
                f"The chain was rewritten (even though its hashes look consistent).")
    for e in entries:
        if e["seq"] not in anchors:
            brk(e["seq"], "UNANCHORED",
                f"Entry #{e['seq']} was never sealed in the external anchor log. It was injected outside the system.")

    # 6. rules replay: would this history have been legal? (only when structure is sound)
    if checks["sequence"] and checks["chain"] and checks["entries"]:
        state = workflow.new_state()
        for e in entries:
            try:
                workflow.apply(state, e)
            except Exception as ex:  # noqa: BLE001
                brk(e["seq"], "RULE_VIOLATION", f"Entry #{e['seq']} breaks the approval rules: {ex}")
                break

    numbered = [b["seq"] for b in breaks if b["seq"] is not None]
    first = min(numbered) if numbered else None
    status = {}
    for e in entries:
        s = e["seq"]
        if any(b["seq"] == s for b in breaks):
            status[s] = "bad"
        elif first is not None and s > first:
            status[s] = "untrusted"
        else:
            status[s] = "ok"
    breaks.sort(key=lambda b: (b["seq"] is None, b["seq"] or 0))
    return {
        "doc_id": doc_id,
        "ok": not breaks,
        "entries": len(entries),
        "anchored": sum(1 for e in entries if e["seq"] in anchors),
        "head": entries[-1]["hash"] if entries else None,
        "checks": checks,
        "breaks": breaks,
        "first_break": first,
        "status": status,
    }


# ----------------------------------------------------------------- checkpoints
def checkpoint() -> dict:
    """A short fingerprint of ALL history so far: the head of the anchor chain.

    Copy it somewhere the database owner cannot edit (email, chat, paper). Later,
    check_checkpoint() proves the log still contains that exact point.
    """
    lines = read_anchor_lines()
    head = lines[-1].get("anchor", GENESIS) if lines and not lines[-1].get("_corrupt") else GENESIS
    return {"ts": now_iso(), "entries": len(lines), "anchor_head": head, "log_ok": anchor_log_ok(lines)}


def check_checkpoint(anchor: str) -> dict:
    """Is a previously saved anchor head still part of the current, unbroken anchor chain?"""
    anchor = (anchor or "").strip().lower()
    lines = read_anchor_lines()
    ok = anchor_log_ok(lines)
    pos = next((i + 1 for i, r in enumerate(lines) if r.get("anchor") == anchor), None)
    if anchor == GENESIS:
        pos = 0
    found = pos is not None and ok
    if not ok:
        msg = "The anchor log itself is broken, so no checkpoint can be trusted."
    elif pos is None:
        msg = "NOT FOUND: this checkpoint is not in the anchor log. History was rewritten or deleted after you saved it."
    else:
        msg = (f"Matches. The log still contains that exact point (entry {pos} of {len(lines)}); "
               f"{len(lines) - pos} later entries were added after it.")
    return {"found": found, "position": pos, "total": len(lines), "log_ok": ok, "message": msg}
