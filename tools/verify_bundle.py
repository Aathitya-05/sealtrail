#!/usr/bin/env python3
"""Offline auditor tool: verify an exported SealTrail audit pack with ONLY the Python standard library.

    python tools/verify_bundle.py DOC-001-audit-pack.json

No server, no database, no trust in the application: the auditor recomputes every hash themselves.
Exit code 0 = intact, 1 = tampering found.
"""
import hashlib
import json
import sys

GENESIS = "0" * 64
FIELDS = ("doc_id", "seq", "ts", "version", "actor", "action", "stage_idx", "comment", "payload", "prev_hash")


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def main(path):
    b = json.load(open(path, encoding="utf-8"))
    problems = []
    prev, expected = GENESIS, 1
    for e in b["ledger"]:
        while expected < e["seq"]:
            problems.append(f"#{expected}: entry missing (deleted)")
            expected += 1
        expected = e["seq"] + 1
        if e["prev_hash"] != prev:
            problems.append(f"#{e['seq']}: chain link broken")
        if sha(canonical({k: e[k] for k in FIELDS})) != e["hash"]:
            problems.append(f"#{e['seq']}: entry edited after sealing")
        prev = e["hash"]
    sealed = {e["version"]: e["payload"].get("content_hash") for e in b["ledger"] if e["action"] in ("SUBMIT", "RESUBMIT")}
    for v in b["versions"]:
        if sha(canonical(v["content"])) != sealed.get(v["version"]):
            problems.append(f"version {v['version']}: content changed after it was sealed")
    by_seq = {e["seq"]: e for e in b["ledger"]}
    for a in b.get("anchors", []):
        e = by_seq.get(a["seq"])
        if e is None:
            problems.append(f"#{a['seq']}: sealed in anchor log but missing from ledger")
        elif e["hash"] != a["hash"]:
            problems.append(f"#{a['seq']}: differs from anchor log (chain rewritten)")
    print(f"{b['doc_id']}: {len(b['ledger'])} entries, {len(b.get('anchors', []))} anchors")
    if problems:
        print("TAMPERING DETECTED")
        for p in problems:
            print("  -", p)
        return 1
    print("OK: history is intact")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: verify_bundle.py <audit-pack.json>")
    sys.exit(main(sys.argv[1]))
