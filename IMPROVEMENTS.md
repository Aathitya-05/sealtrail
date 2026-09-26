# Exact improvements, in priority order

Already fixed in this version: `/api/verify` (Audit all) now also covers a document whose records were deleted entirely, by checking every document the anchor log knows about (test added).

## A. Do before the demo (10 minutes, no code)
1. Click **Reset demo data** in the Tamper lab right before you present, so every document is clean.
2. Rehearse the 3-minute script in `docs/EXPLANATION.md` section 7 twice. Know which button turns Verify red.
3. Start the server before the judges arrive; keep the browser on DOC-002.

## B. DONE (attachments implemented; see app/files.py). Original notes: Biggest gap versus the problem statement (do first if you have time, about 25 minutes)
**Real file attachments.** The statement says "document", and today a document is only title, vendor, amount and description.
- Add `POST /api/documents/{id}/attachment` (multipart) storing the file under `data/files/<sha256>`.
- Put `{"filename", "sha256", "size"}` inside the document **content**, so it is covered by the existing content seal. Then swapping the PDF after approval is detected as `CONTENT_EDITED` with no other change to the ledger.
- In `verify()`, also re-hash the stored file and compare with the sealed `sha256`.
- UI: file input in the New document and Resubmit forms; show filename plus short hash in the header.
- Add a test: replace the stored file, expect `CONTENT_EDITED`.

## C. Strengthen the story (15 to 20 minutes each)
1. **DONE (Checkpoint card, see ledger.checkpoint).** Original: **Checkpoint button:** show the current head hash with a Copy button ("paste this into an email or chat; it is your external anchor"). Makes the anchor idea tangible.
2. **Requester withdraw:** new action `WITHDRAW` (requester, only while IN_PROGRESS) moving to a WITHDRAWN status. Add to `apply()`, the pipeline and tests.
3. **Overdue nudges:** highlight a stage as overdue after N hours in "Why is it stuck?" (data already exists: `stage_since`).
4. **Delegation:** approver on leave delegates to another user (a sealed `DELEGATE` entry), the honest answer to "what if the approver is away?".

## D. Production-only (mention as future work, do not build for the hackathon)
- Real login (SSO) and a per-user digital signature on every entry instead of a plain actor id.
- Anchor log moved to storage the DB owner cannot edit (email, write-once object storage, a notary or transparency log).
- PostgreSQL with row-level locking; background verification job that alerts on the first break.
- User names and roles are not sealed; renaming a user in the database changes what is displayed (the actor id in the ledger stays correct).

## E. Small polish
- Show the anchored count on each document card after "Audit all".
- Add keyboard shortcut or focus handling to the Tamper lab summary.
- Add a screenshot set to the README for the submission.
