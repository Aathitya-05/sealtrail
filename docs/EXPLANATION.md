---
title: "SealTrail: Tamper-Evident Document Approval Platform"
subtitle: "Hackathon Problem Statement SD-06 - Explanation Document"
---

# 1. Problem statement (SD-06)

Build a document approval system with **sequential and parallel approval stages**. A document moves forward only when the conditions of its current stage are satisfied. Users must be able to **reject, comment, resubmit revised versions, and review complete approval history**.

# 2. The real-world problem we solve

Every organisation approves money and contracts: purchase orders, tenders, expense claims, policies. The approval history is normally just rows in a database table. That creates a trust problem:

1. A purchase order of ₹5,00,000 is approved by Manager, Finance Head, Legal and Procurement.
2. Months later an audit finds the money went to the wrong vendor.
3. Someone with database access quietly edits the record: a different approver name, a changed amount, a deleted rejection.
4. With a normal history table **nobody can tell the record was edited**. Blame lands on the wrong person and fraud goes undetected.

Existing tools show *who approved*. They cannot prove *that the record was never changed*. Government offices, hospitals (SOP approvals), banks and colleges all need that proof.

# 3. Our solution in one line

> **Approval history that cannot be silently edited. Every decision is chained to the one before it, so any tampering is detectable and pinpointed.**

# 4. How it works (simple explanation)

**Analogy: a bank passbook where every line is stamped with the line above it.** Change one line and every stamp after it stops matching.

| Idea | What it means | What it stops |
|---|---|---|
| **Hash chain** | Every action (submit, approve, reject, comment, resubmit) is one record. Each record stores a SHA-256 fingerprint of itself plus the previous record's fingerprint. | Editing or deleting any record in the middle |
| **Content seal** | The document's text, amount and attached file (its SHA-256) are fingerprinted at submission. Every approval repeats that fingerprint. | Changing the amount or text, or swapping the attached PDF, after it was approved |
| **External anchor log** | Each fingerprint is also copied into a separate append-only log. | An admin who rewrites the whole chain and recomputes every hash |
| **State is derived, never stored** | There is no "status" field. Status is recomputed by replaying the history through the approval rules. | Flipping a status flag directly in the database |
| **Rules replay** | The auditor re-runs the same approval rules over the history to check every action was legal. | Forged entries that break the sequence or approver rules |

# 5. Approval rules (what the system enforces)

- **Stages are configurable data:** each stage has a mode (**Sequential** or **Parallel**), a rule (**All** or **Any one**), and a list of approvers.
- **Sequential:** approvers must act in order; someone who tries early is refused.
- **Parallel + All:** everyone can act in any order; the stage completes when the last approves.
- **Parallel + Any:** the first approval completes the stage and releases the others.
- **Reject:** needs a comment; the document becomes Rejected immediately.
- **Resubmit:** only the requester, only after rejection, and something must actually change. It creates **version 2**, restarts from stage 1, and keeps all old history.
- **Comments:** anyone can comment at any time; comments are sealed in the ledger too.
- **No self-approval:** the requester can never approve their own document (segregation of duties).
- **Why is it stuck?** A panel names exactly who is pending and why.
- **Time travel:** a slider replays the document as it was after any entry.

# 6. What makes it different from the real world

| Typical approval tool | SealTrail |
|---|---|
| History is editable rows | History is a hash chain; edits are detected and located |
| Status stored in a field | Status derived from history; nothing to flip |
| "Approved" says nothing about what was approved | Each approval is sealed to the exact content version |
| Auditor must trust the database owner | Auditor gets a downloadable audit pack and verifies it **offline** with a 50-line script |
| Rules checked only at click time | Rules checked at click time **and** again during audit |

# 7. Demo script (3 minutes)

1. **Scenario:** a purchase order workflow. Stage 1 sequential (Manager then Finance Head); Stage 2 parallel, all (Legal and Procurement).
2. Open **DOC-002**. The "Why is it stuck?" panel says it is waiting on Procurement. Switch user to Karthik and approve. The document becomes Approved.
3. Open **DOC-003** to show a **reject, then revise, then resubmit as v2** story with all history preserved.
4. Click **Verify history**: green, with six checks passed.
5. **Uploads:** open **DOC-001**, click **Download** on the attached PDF (works). Open the **Tamper lab** and click "Swap the uploaded file". Click **Download** again: it is refused with the seal message. Click **Verify history**: **red**, `FILE_EDITED` at entry #1 (chain and anchors stay green, so only the file changed). Click **Reset demo data**.
   - Then try "Change who approved": **Verify history** goes red and names the exact entry. Reset again.
6. Click "Rewrite the whole chain" (the smart attacker). The hashes look consistent, but the **external anchors** still catch it.
7. (Optional, 20 seconds) Click **Get checkpoint** and **Copy**: "this fingerprint of all history lives in my email, outside the database." Paste it into **Check it** after the attack and it reports NOT FOUND if history was rewritten and re-chained.
8. Close with the pitch line.

# 8. Likely judge questions

**Is this blockchain?** No. It uses the same tamper-evidence idea (hash chaining) with no network or mining, so it is fast and simple.

**Can't an admin recompute all the hashes?** Yes, and that is why every hash is also written to an external anchor log. The rewritten chain no longer matches it. In production that log lives outside the database owner's control (email, write-once storage, or a notary).

**What if they delete the last entry?** The chain alone cannot see that, but the anchor log remembers it existed. The tool reports the missing entry.

**Why not just use audit logging?** Ordinary audit logs can be edited by whoever controls the database. Ours proves whether they were.

**Does it slow things down?** One SHA-256 hash per action, which is negligible.

# 9. Technology used

Python and FastAPI (backend), SQLite (database), SHA-256 via `hashlib` (fingerprints), one HTML page with plain JavaScript (interface). 44 automated tests cover the rules and every tampering attack, including two users approving at the same instant.

# 10. Honest limitations and next steps

- No real login; a user dropdown stands in for it. Production would use single sign-on and per-user digital signatures on each entry.
- The anchor log is a local file in this demo; it should be moved to independent storage.
- Documents can carry an uploaded file (PDF, Word, Excel, PowerPoint, image, text; 10 MB max). It is stored on local disk by its SHA-256 and sealed through the document content. The audit pack carries the sealed hash; `tools/verify_bundle.py --files <dir>` re-hashes the files offline.
- SQLite with a single write lock suits a demo; production would use PostgreSQL.
- Tamper-*evidence* detects and locates changes; it does not prevent someone with database access from making them.

# 11. One-line pitch

**"We solve the trust problem in approvals: not just who approved, but proof that the record was never changed."**
