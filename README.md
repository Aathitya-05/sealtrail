# SealTrail: tamper-evident document approvals (SD-06)

Sequential and parallel approval stages, reject, comment, resubmit as new versions, full history, and a **hash-chained ledger** that proves the history was never edited.

## Run

```bash
pip install -r requirements.txt
python run.py            # open http://localhost:8000
python -m pytest -q      # 44 tests
```

The first start seeds four purchase orders automatically. Use the **Acting as** dropdown to switch user (no login by design; hackathon simplification).

## Try the demo

1. DOC-002: switch to *Karthik S* and approve to complete it.
2. DOC-003: a reject, revise, resubmit (v2) story.
3. Click **Verify history** (green), then open **Tamper lab**, click an attack, click **Verify history** again (red, names the entry).
4. **Download audit pack**, then `python tools/verify_bundle.py DOC-001-audit-pack.json` verifies it offline with only the standard library.

## Layout

`app/ledger.py` hashing, anchors and verify. `app/workflow.py` rules engine. `app/tamper.py` demo attacker. `app/main.py` API. `app/static/` UI. `docs/` implementation plan and explanation.

Set `SEALTRAIL_DEMO=0` to disable the tamper and reset endpoints.

## Uploads
Attach a PDF, Word, Excel, PowerPoint, image or text file (10 MB max) when creating or resubmitting a document. The file is stored by its SHA-256 and sealed with the document, so swapping it after approval is caught by **Verify history**.

Upload demo: open DOC-001, click **Download** on the attached PDF, then Tamper lab > **Swap the uploaded file**, click **Download** again (refused), then **Verify history** (red, `FILE_EDITED` at entry #1). Offline: `python tools/verify_bundle.py pack.json --files data/files`.

## Checkpoint
**Get checkpoint** shows a one-line fingerprint of all history (the head of the anchor chain) with a Copy button. Save it outside the database (email, chat). Later, paste it into **Check it**: it reports NOT FOUND if history was truncated or rewritten since. API: `GET /api/checkpoint`, `POST /api/checkpoint/check`.

## Look
The UI uses a "notary's ledger" theme (paper, ink, wax-seal red; dark mode follows the system). Styles are in `app/static/style.css`.
