# SealTrail: project context (read this first)

Hackathon project for **SD-06 Collaborative Document Approval Platform** (Sun Info Media, CIT campus recruitment, Round 2). Owner: Aathitya. Time budget for the original build was 90 minutes; the hackathon is judged on a working demo, uniqueness, and easy explanation.

## The idea in one line
Approval history is a **hash-chained, tamper-evident ledger**. State is never stored; it is derived by replaying the ledger through the rules engine. Verify names the exact tampered entry.

## Where things are
- `app/ledger.py` hashing, external anchor log, `verify()` (six checks)
- `app/workflow.py` rules engine: `apply()`, `fold()`, `perform()`, `document_view()`
- `app/files.py` content-addressed attachment store (`data/files/<sha256>`); `verify()` re-hashes it
- `app/tamper.py` DEMO-ONLY attacker (raw SQL). Never call from normal code paths
- `app/main.py` FastAPI routes; `app/static/` single-page UI (vanilla JS, no build)
- `tools/verify_bundle.py` offline auditor (stdlib only)
- `tests/` 41 pytest tests. Run `python -m pytest -q`
- `docs/IMPLEMENTATION_PLAN.md` full spec; `docs/EXPLANATION.md` judge-facing explanation

## Rules to keep intact (do not "simplify" these away)
1. No `status` column anywhere. Status = `fold(ledger)`.
2. The ONLY write path for history is `ledger.append()` (chain + insert + anchor), inside `db.WRITE_LOCK` and `BEGIN IMMEDIATE`.
3. `perform()` dry-runs `apply()` on a deep copy before writing. The same `apply()` is reused by `verify()`.
4. Workflow stages live inside the SUBMIT/RESUBMIT payload (sealed), not in a separate editable table.
5. Requester can never approve their own document.
6. Hash input field list is `ledger.HASH_FIELDS`. Changing it invalidates every existing hash and `tools/verify_bundle.py` must change with it.

## Run
`pip install -r requirements.txt && python run.py` then open http://localhost:8000. First start seeds 4 purchase orders. `SEALTRAIL_DEMO=0` disables tamper/reset endpoints.

## Known limits (be honest about these)
No real auth (user dropdown); anchor log is a local file; attachments are stored on local disk (data/files/<sha256>, 10 MB cap, one file per version) and are not in the offline audit pack (tools/verify_bundle.py checks the sealed hash only, not the bytes); SQLite + single write lock.

See `IMPROVEMENTS.md` for the prioritized to-do list.
