#!/usr/bin/env python3
"""Offline auditor tool: verify an exported SealTrail audit pack with ONLY the Python standard library.

    python tools/verify_bundle.py DOC-001-audit-pack.json [--files <dir>]

--files <dir>  also re-hash every attached file (named by its sha256) found in <dir>, e.g. data/files.
               Without it, attached files are NOT checked (only their sealed hashes are).

No server, no database, no trust in the application: the auditor recomputes every hash themselves.
Exit code 0 = intact, 1 = tampering found.
"""
import hashlib
import json
import os
import sys

GENESIS = "0" * 64
FIELDS = ("doc_id", "seq", "ts", "version", "actor", "action", "stage_idx", "comment", "payload", "prev_hash")


def canonical(o):
    return json.dumps(o, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha(s):
    return hashlib.sha256(s.encode()).hexdigest()


def main(path, files_dir=None):
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
    atts = [(v["version"], v["content"]["attachment"]) for v in b["versions"] if isinstance(v.get("content"), dict) and v["content"].get("attachment")]
    if files_dir:
        for ver, a in atts:
            p = os.path.join(files_dir, str(a.get("sha256")))
            if not os.path.isfile(p):
                problems.append(f"version {ver}: FILE_MISSING, attached file {a.get('filename')} is not in {files_dir}")
            elif hashlib.sha256(open(p, "rb").read()).hexdigest() != a["sha256"]:
                problems.append(f"version {ver}: FILE_EDITED, attached file {a.get('filename')} was replaced after it was sealed")
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
    if atts and not files_dir:
        print(f"Note: {len(atts)} attached file(s) were NOT checked. Re-run with --files <dir> to re-hash them.")
    elif atts:
        print(f"Checked {len(atts)} attached file(s) against their sealed sha256.")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    fdir = None
    if "--files" in args:
        i = args.index("--files")
        if i + 1 >= len(args):
            sys.exit("--files needs a directory")
        fdir = args[i + 1]
        del args[i:i + 2]
    if len(args) != 1:
        sys.exit("usage: verify_bundle.py <audit-pack.json> [--files <dir>]")
    sys.exit(main(args[0], fdir))
