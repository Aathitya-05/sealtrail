# SealTrail: Implementation Plan (paste into Google Antigravity)

> **Role:** Act as a senior full-stack software developer. Build the project described below exactly as specified. A complete reference implementation already exists in this folder (`app/`, `tests/`, `tools/`). If you are starting from an empty folder, build it from this plan. If the code is already here, review it against this plan, run the tests, and fix any gap.

## 1. Goal

Hackathon problem statement **SD-06, Collaborative Document Approval Platform**:

> Develop a document approval system supporting sequential and parallel approval stages. A document may move forward only when the approval conditions for its current stage are satisfied. Users must be able to reject, comment, resubmit revised versions, and review complete approval history.

**Unique twist:** normal systems store approval history as editable rows, so an insider with database access can change who approved what, and nobody can prove it. SealTrail makes the history **tamper-evident**:

1. Every action is a **hash-chained ledger entry** (each entry contains the hash of the previous one).
2. Every approval is **sealed to the exact document content** it approved (content hash).
3. Every entry is also copied into an **external anchor log**, so even a full chain rewrite is detected.
4. Document **state is never stored**. It is always derived by replaying the ledger through the rules engine.
5. **Verify** button names the exact entry that was tampered with. A **Tamper Lab** lets the demo play the attacker.

Real-world scenario used for the demo: **Purchase Order approval** in a company (finance, legal, procurement, HR).

## 2. Tech stack (no build step, runs anywhere)

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Database | SQLite (stdlib `sqlite3`) |
| Hashing | `hashlib.sha256` |
| Frontend | One HTML page, plain CSS, vanilla JS (`fetch`), no framework |
| Tests | pytest, httpx |

Run: `pip install -r requirements.txt && python run.py` then open `http://localhost:8000`.

## 3. Folder structure

```
sealtrail/
  run.py                    # uvicorn launcher
  requirements.txt
  app/
    db.py                   # connection, schema, write lock, reset
    ledger.py               # hashing, append, anchors, verify()
    workflow.py             # rules engine: apply(), fold(), perform(), views
    seed.py                 # demo users + 4 purchase-order stories
    tamper.py               # DEMO ONLY attacker functions (raw SQL)
    main.py                 # FastAPI routes
    static/index.html, style.css, app.js
  tools/verify_bundle.py    # offline auditor tool, stdlib only
  tests/conftest.py, test_workflow.py, test_ledger.py
  docs/
```

## 4. Data model (SQLite)

Note: **no `status` column exists anywhere.**

```sql
users(id TEXT PK, name TEXT, role TEXT, title TEXT)
documents(id TEXT PK, created_at TEXT)
versions(doc_id TEXT, version INTEGER, content_json TEXT, content_hash TEXT, created_at TEXT, PRIMARY KEY(doc_id, version))
ledger(doc_id TEXT, seq INTEGER, ts TEXT, version INTEGER, actor TEXT, action TEXT,
       stage_idx INTEGER, comment TEXT DEFAULT '', payload_json TEXT,
       prev_hash TEXT, hash TEXT, PRIMARY KEY(doc_id, seq))
```

Document content JSON: `{title, description, vendor, amount}` (amount is an integer in rupees).

Roles/users seeded: two requesters (Aarav, Priya), Manager (Meera), Finance Head (Rahul), Legal (Sneha), Procurement (Karthik), HR (Divya), Auditor (Ishaan). There is no login; a "Acting as" dropdown selects the current user (state this as a hackathon simplification).

## 5. The ledger (core idea)

**Actions:** `SUBMIT`, `APPROVE`, `REJECT`, `COMMENT`, `RESUBMIT`.

**Canonical JSON:** `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)`.

**Entry hash:**

```
hash = SHA256( canonical({doc_id, seq, ts, version, actor, action,
                          stage_idx, comment, payload, prev_hash}) )
```

- `prev_hash` of entry #1 is 64 zeros (genesis). Otherwise it equals the previous entry's `hash`.
- `payload` for `SUBMIT`/`RESUBMIT`: `{owner, stages, content_hash, changed?}`. The **workflow configuration is stored inside the ledger**, so it cannot be edited separately.
- `payload` for `APPROVE`/`REJECT`: `{content_hash}` (the approval is bound to that exact content).
- `content_hash = SHA256(canonical(content))`.

**External anchor log** (`data/anchors.log`, separate file, append-only JSON lines, itself hash-chained):

```
{"ts","doc_id","seq","hash","prev","anchor"}   anchor = SHA256(prev|doc_id|seq|hash)
```

One line is written after every entry. In production this would be email, WORM storage, or a notary service.

**Appending is the only write path:** `append()` builds the entry, inserts it, and writes the anchor, all inside a global write lock and a `BEGIN IMMEDIATE` transaction.

## 6. Rules engine (`workflow.py`)

State is a pure function: `state = fold(apply, entries)`.

A stage is `{name, mode: SEQUENTIAL|PARALLEL, rule: ALL|ANY, approvers: [user ids]}`. A `SEQUENTIAL` stage always behaves as ALL, in order.

`apply(state, entry)` raises `PolicyError` when the entry is illegal. Rules:

| Action | Allowed when |
|---|---|
| SUBMIT | state is NEW, version 1, actor is the owner, content hash present, stages valid |
| APPROVE / REJECT | status IN_PROGRESS; entry version and stage equal the current ones; actor is a listed approver of the current stage; actor is **not the owner** (segregation of duties); actor has not already decided; if stage is SEQUENTIAL, actor is the **next undecided** approver |
| APPROVE extra | payload `content_hash` equals the current version's content hash |
| REJECT extra | comment is mandatory. Status becomes REJECTED immediately |
| Stage completion | SEQUENTIAL or PARALLEL+ALL: all approved. PARALLEL+ANY: first approval. Then `stage_idx += 1`; past the last stage the status is APPROVED |
| COMMENT | any user, any status except NEW, non-empty text, no state change |
| RESUBMIT | status REJECTED; actor is the owner; version = previous + 1; content **must actually change**; stages copied; decisions cleared; stage 0; old versions and history kept |

Also expose: `waiting_now(state)`, `pipeline(state)` (per-stage and per-approver status), `explain(state)` (the "Why is it stuck?" text), `document_view(doc, at=N)` (time-travel replay of the first N entries), and `fold(strict=False)` which stops at the last trustworthy entry and reports where replay broke.

`perform()` runs `apply()` on a deep copy first as a dry run, so illegal actions are rejected before anything is written. The same `apply()` is reused by the auditor.

## 7. Verification (`ledger.verify`)

Return `{ok, entries, anchored, head, checks, breaks[{seq,code,message}], first_break, status{seq: ok|bad|untrusted}}`. Checks:

1. **Sequence:** seq numbers are 1..n with no gaps (`ENTRY_MISSING`).
2. **Chain:** each `prev_hash` equals the previous stored hash (`LINK_BROKEN`).
3. **Entry hash:** recompute and compare (`ENTRY_EDITED`).
4. **Content seal:** for each version, recompute `content_hash` and compare with the hash sealed in its SUBMIT/RESUBMIT entry (`CONTENT_EDITED`).
5. **Anchors:** every anchor must match an entry hash (`ANCHOR_MISMATCH`, `ANCHOR_MISSING_ENTRY` for deleted tail), every entry must have an anchor (`UNANCHORED`), and the anchor log's own chain must be intact (`ANCHOR_LOG_TAMPERED`).
6. **Rules replay:** run `apply()` over all entries (only if checks 1 to 3 passed); an illegal entry is `RULE_VIOLATION`.

Entries after the first break are marked `untrusted`.

## 8. API

| Method and path | Purpose |
|---|---|
| GET `/api/users`, `/api/templates`, `/api/config` | Reference data |
| GET `/api/documents` | List with derived status, stage, waiting-on |
| GET `/api/documents/{id}?at=N` | Full view (pipeline, why-stuck, entries), optional time travel |
| POST `/api/documents` | Create: `{owner, content, stages? \| template?}` |
| POST `/api/documents/{id}/actions` | `{actor, action, comment, content?}` returns new entry and view |
| GET `/api/documents/{id}/verify`, `/api/verify` | Verify one or all |
| GET `/api/documents/{id}/export` | Audit pack JSON (versions + ledger + anchors + verification) |
| POST `/api/demo/tamper` `{doc_id, mode}` | Modes: `edit_actor`, `edit_comment`, `edit_content`, `delete_entry`, `forge_approval`, `rewrite_chain` |
| POST `/api/demo/reset` | Re-seed everything (disable with `SEALTRAIL_DEMO=0`) |

Errors: `PolicyError` returns HTTP 409 `{detail}`; not found returns 404.

## 9. Seed data (Purchase Orders)

Standard workflow: Stage 1 "Management Sign-off" SEQUENTIAL (Meera then Rahul), Stage 2 "Compliance Review" PARALLEL ALL (Sneha, Karthik).

| Doc | Story |
|---|---|
| DOC-001 Laptops, 5,00,000 | Fully approved (used for the tamper demo) |
| DOC-002 Office renovation, 12,00,000 | Stuck: waiting on Karthik (shows "Why is it stuck?") |
| DOC-003 Cloud hosting, 2,40,000 | Rejected by Rahul, revised, resubmitted as v2, awaiting Rahul |
| DOC-004 Team offsite, 80,000 | "Quick" template: one PARALLEL ANY stage (Meera or Rahul), untouched |

Other templates: HR policy (HR + Legal parallel ALL, then Finance Head).

## 10. UI requirements (single page)

- Header with product name and **"Acting as"** user dropdown.
- Sidebar: document cards (status pill, amount, version, "waiting on…", chain badge after verify) plus **New document** and **Audit all**.
- Document header: title, version pill, amount, requester, vendor.
- **Approval pipeline:** stage cards with mode label, per-approver state (approved, rejected, waiting/pulsing, queued with order number, not needed).
- **Why is it stuck?** callout (colour by tone).
- **Your actions:** Approve/Reject (with comment) only if the acting user can act now; otherwise a sentence explaining why not; requester sees a **Resubmit** form when rejected; everyone can post a comment.
- **Time travel** slider that replays state at any entry (read-only).
- **Verify history** button, result panel with six check chips, and **Download audit pack**.
- **Ledger timeline:** every entry with action badge, actor, comment, `prev` and `hash` chips, content-seal chip; entries turn red/dashed-amber after verify; deleted entries appear as a red "MISSING" ghost row.
- **New document dialog** with template select and a **stage builder** (name, mode, rule, approver checkboxes, add/remove stage).
- **Tamper lab** (collapsible, dashed red) with the six attacks and Reset.
- Accessible (labels, focus rings), works at phone width, supports dark mode.

## 11. Tests that must pass (44 in the current reference)

- Sequential order enforced; parallel ALL any order; parallel ANY first approval completes and releases others.
- Only listed approvers; requester cannot approve own document; no double approval.
- Reject needs a comment and blocks further approvals; parallel reject beats partial approvals.
- Comment allowed for anyone but not empty.
- Resubmit: owner only, only when rejected, must change something, creates v2, restarts at stage 1, keeps all history.
- No `status` column in any table.
- **Concurrency:** two threads approve a PARALLEL-ANY stage at once; exactly one succeeds.
- Time-travel replay; "why stuck" names the right person.
- Clean ledgers verify. Chain links are real.
- Each attack is detected and located: edited approver, edited comment, deleted middle entry, deleted last entry (anchors), edited content, **full chain rewrite** (anchors), forged rule-valid approval (unanchored), tampered anchor log.
- Offline `tools/verify_bundle.py` passes on a clean pack and flags `#2` on an edited one.

## 12. Acceptance checklist

- [ ] `python run.py` starts and seeds four documents with no manual setup.
- [ ] Full flow works in the browser: submit, approve (sequential then parallel), reject with comment, resubmit v2.
- [ ] Verify is green on untouched documents.
- [ ] Each Tamper Lab button turns Verify red and points to the correct entry.
- [ ] All 44 tests pass (`python -m pytest -q`).

## 13. Suggested 90-minute build order

| Minutes | Task |
|---|---|
| 0-10 | Schema, `db.py`, canonical JSON and hash helpers |
| 10-35 | `apply()`/`fold()` rules, `perform()`, `create_document()` |
| 35-50 | FastAPI routes, seed data |
| 50-70 | UI: list, pipeline, actions, ledger timeline |
| 70-80 | `verify()`, anchors, Verify panel |
| 80-90 | Tamper lab, rehearsal, README |

## 14. Known limits (say these honestly to judges)

- No real authentication (user dropdown). Production would use SSO and sign entries with per-user keys.
- The anchor log is a local file here; in production it must live outside the database owner's control (email, object-lock storage, or a notary).
- SQLite and one write lock suit a demo; a production system would use Postgres with row-level locking.

## 15. File uploads (added after the first build)
- **Storage:** `app/files.py`. Files are saved as `db.FILES_DIR/<sha256 of the bytes>` (default `data/files`). User input never becomes a path.
- **Sealing:** the optional `content["attachment"] = {filename, sha256, size}` sits inside the document content, so the existing content hash seals it, and every APPROVE already binds to it. `ledger.HASH_FIELDS` is unchanged. A resubmission that changes only the file counts as a change (`payload["changed"]` contains `attachment`).
- **API:** `POST /api/uploads?filename=x` (raw body, max 10 MB, types pdf/docx/xlsx/pptx/png/jpg/jpeg/txt, filename sanitized, HTTP 400 on rejection). `GET /api/documents/{id}/file?version=N` re-hashes the stored file and returns 409 `The attached file no longer matches the hash sealed at entry #k` if it differs; otherwise it downloads it (`Content-Disposition: attachment`, `nosniff`, never inline).
- **Verification:** in the content-seal step, `verify()` re-hashes the file of every SUBMIT/RESUBMIT version: `FILE_MISSING` and `FILE_EDITED`, both under the "content" check, attached to the seal entry's seq.
- **Tamper lab:** mode `swap_file` overwrites the stored bytes with different ones (raw file write).
- **Offline:** `tools/verify_bundle.py pack.json --files <dir>` re-hashes attached files; without the flag it says they were not checked.
- **Seed:** DOC-001 has a PDF quote; DOC-003 has a different PDF on v1 and v2; DOC-002 and DOC-004 have none (the field is optional).
- **UI:** file input on New document and Resubmit (upload first, then send `content.attachment`), an attachment chip with Download (turns red on 409), and the attachment shown on SUBMIT/RESUBMIT ledger entries.

## 16. Checkpoint (added after the first build)
- `ledger.checkpoint()` returns `{ts, entries, anchor_head, log_ok}`: the head of the anchor chain, a fingerprint of ALL history. `ledger.check_checkpoint(anchor)` reports whether a saved head is still inside the current, unbroken anchor chain (found, position, total, message).
- API: `GET /api/checkpoint`, `POST /api/checkpoint/check {anchor}`. UI: a Checkpoint card with Get checkpoint, Copy and Check it.
- Honest limit: it only helps if the copy is kept somewhere the database owner cannot edit. Tests: `tests/test_checkpoint.py`.

## 17. UI theme
A notary's-ledger look in `app/static/style.css` (no external fonts, no build step): paper background, navy ink, wax-seal red accent, serif headings, coloured status stripes on document cards, dark mode via `prefers-color-scheme`.
