# SealTrail: tamper-evident document approvals

**Hackathon problem statement SD-06: Collaborative Document Approval Platform** (Sun Info Media campus recruitment, Round 2)

> Approval history that cannot be silently edited. Every decision is chained to the one before it, so any tampering is detected and pinpointed to the exact entry.

## The problem
Approval history is normally just rows in a database, so anyone with database access can quietly change who approved what, or swap the approved document. Nobody can tell. SealTrail makes that detectable.

## What it does
**Approval workflow (the SD-06 requirements)**
- Sequential and parallel approval stages (parallel stages need **all** approvers or **any one**).
- A document moves forward only when the conditions of its current stage are satisfied.
- Approve, reject (a reason is required), comment, and resubmit a revised version (v2, v3...). Old versions and history are kept.
- Complete approval history, a "Why is it stuck?" panel, and a time-travel slider that replays the document at any point.
- The requester can never approve their own document.

**Tamper evidence (what makes it different)**
- **Hash chain:** every action is one ledger entry holding the SHA-256 of the previous entry.
- **Content seal:** each approval is sealed to the exact document content and attached file it approved.
- **External anchor log:** every entry hash is also copied to a separate chained log, so even a full chain rewrite is detected.
- **Derived status:** status is never stored; it is recomputed by replaying the ledger through the rules engine.
- **Verify** names the exact tampered entry. A **Tamper lab** plays the insider attacker for demos.
- **Real file uploads** (PDF, Word, Excel, PowerPoint, image, text; 10 MB max), stored by SHA-256 and covered by the seal. Swapping the file after approval is detected, and the download is refused.
- **Checkpoint:** copy a one-line fingerprint of all history outside the database and check it later.
- **Offline audit pack:** `tools/verify_bundle.py` re-verifies an exported pack with only the Python standard library.

## Tech stack
Python 3 · FastAPI · Uvicorn · Pydantic · SQLite · SHA-256 (`hashlib`) · plain HTML/CSS/JavaScript (no framework, no build step) · pytest.

## Run it
```bash
pip install -r requirements.txt
python run.py            # then open http://localhost:8000
python -m pytest -q      # 44 tests
```
The first start seeds four purchase orders. Use the **Acting as** dropdown to switch user (no login, by design for the demo). Set `SEALTRAIL_DEMO=0` to disable the Tamper lab and reset endpoints.

## Try the demo (2 minutes)
1. **DOC-002:** switch **Acting as** to *Karthik S* and click **Approve**.
2. **DOC-003:** a reject, revise, resubmit (v2) story with a different file on each version.
3. **DOC-001:** click **Verify history** (green). Open **Tamper lab**, click **Swap the uploaded file**, then **Download** (refused) and **Verify history** (red, `FILE_EDITED` at entry #1). Click **Reset demo data**.
4. Tamper lab > **Rewrite the whole chain**, then **Verify history**: the chain looks consistent but the external anchors catch it.

Full click-by-click script: [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md).

## Project layout
| Path | What it is |
|---|---|
| `app/ledger.py` | Hashing, anchor log, `verify()`, checkpoints |
| `app/workflow.py` | Approval rules engine (`apply`, `fold`, `perform`) |
| `app/files.py` | Content-addressed upload store |
| `app/main.py` | FastAPI routes |
| `app/tamper.py` | Demo-only attacker (raw SQL and file writes) |
| `app/seed.py` | Demo data |
| `app/static/` | Single-page UI (vanilla JS) |
| `tools/verify_bundle.py` | Offline auditor for exported audit packs |
| `tests/` | 44 automated tests |
| `docs/` | Implementation plan, explanation, demo guide (Markdown and Word) |

## Design rules (kept on purpose)
- No `status` column anywhere: status is derived from the ledger.
- `ledger.append()` is the only write path for history (chain + insert + anchor, under a write lock).
- The same rules function runs before an action is recorded and again when history is audited.
- Workflow stages are sealed inside the SUBMIT/RESUBMIT entry, not in an editable table.

## Honest limitations
- No real authentication; a user dropdown stands in for login. Production would use SSO and per-user signatures.
- The anchor log is a local file here. In production it belongs on storage the database owner cannot edit.
- Tamper-*evidence* detects and locates changes; it does not prevent someone with database access from making them.
- SQLite with a single write lock suits a demo; production would use PostgreSQL.

## Documentation
- [docs/EXPLANATION.md](docs/EXPLANATION.md): the explanation for judges (Word copy: `docs/SealTrail_Explanation.docx`)
- [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md): full specification
- [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md): live demo script (Word copy: `docs/SealTrail_Demo_Guide.docx`)
- [CLAUDE.md](CLAUDE.md) and [IMPROVEMENTS.md](IMPROVEMENTS.md): project rules and what is left to do
