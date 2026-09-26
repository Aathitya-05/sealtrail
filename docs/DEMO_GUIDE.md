---
title: "SealTrail: Live Demo Guide"
subtitle: "Click-by-click script for the SD-06 hackathon demo (about 3 minutes)"
---

# Before you start

1. Run `python run.py`, then open http://localhost:8000.
2. Scroll to the very bottom of any document and click the red dashed bar **"Tamper lab"**. It opens.
3. Click **Reset demo data** (the last button in that box). A toast says "Demo data reset."
4. Scroll back to the top and leave it there.

**Page layout, top to bottom:**

- The header bar, with **Acting as** at the top right.
- The document list on the left (above the document on a narrow window).
- The selected document: Approval pipeline, Why is it stuck?, Your actions, Time travel, Verify, Checkpoint, Approval ledger, Tamper lab.

# Part 1: The problem (say only, no clicks)

> "Companies approve money and contracts. The history is normally rows in a database, so whoever controls the database can quietly change it. SealTrail makes that impossible to hide."

# Part 2: The approval workflow

1. Click **DOC-002 "Office renovation contract"** in the list. You will see stage 1 (Management Sign-off) DONE and stage 2 (Compliance Review) ACTIVE: Sneha has a green tick and Karthik glows "can act now". Say: "Stage 1 is sequential, in order. Stage 2 is parallel, and all must approve."
2. Scroll to the **Why is it stuck?** panel. It says "Waiting on Karthik S". Say: "It always tells you exactly who is holding the document up."
3. Top right, open the **Acting as** dropdown and choose **Karthik S, Procurement Lead**.
4. Scroll to **Your actions**. You will see "Your decision is needed" with **Approve** and **Reject**. If there is no Approve button, you are not acting as Karthik, or DOC-002 is already approved: choose Karthik or click **Reset demo data**.
5. Click in the comment box and type: `Best price, terms agreed.`
6. Click the green **Approve** button. A toast says "APPROVE sealed as entry #5" and the status becomes **APPROVED**.

# Part 3: Reject, revise, resubmit (DOC-003)

1. Click **DOC-003 "Cloud hosting renewal"** in the list.
2. Scroll to **Approval ledger** and go through the entries: SUBMIT, APPROVE, REJECT (with a reason), COMMENT, RESUBMIT (v2), APPROVE. Point at the RESUBMIT line "Changed: amount, attachment, description". Say: "Rejected, revised, resubmitted as version 2 with a different attached PDF. Nothing from v1 was erased."
3. Scroll up to **Time travel** and drag the slider to about the third position. The status becomes **REJECTED**. Drag it all the way right to return to Live. Say: "I can replay the document as it was after any entry."

# Part 4: The hash chain and Verify (DOC-001)

1. Click **DOC-001 "Laptops for 10 new hires"**.
2. Point at the paperclip strip: `Dell-quote-Q2291.pdf` with its sealed hash and a **Download** button.
3. Scroll to **Approval ledger** and point at one entry. Say: "Each entry stores the fingerprint of the one before it (prev, then hash), and each approval repeats the content seal."
4. Scroll to the **Verify** card and click **Verify history**. You will see a green box "HISTORY VERIFIED" with six green checks.

# Part 5: Attack 1, swap the uploaded file

1. Scroll up to the paperclip strip and click **Download**. A toast says "File downloaded. Its bytes matched the sealed hash."
2. Scroll to the very bottom and click the red dashed **Tamper lab** bar to open it.
3. Click the **Swap the uploaded file** box. A toast says the file was replaced after approval.
4. Scroll up and click **Download** again. A red toast says "The attached file no longer matches the hash sealed at entry #1" and the strip turns red with **File altered**.
5. Scroll to **Verify** and click **Verify history**. You will see red "TAMPERING DETECTED, first break at entry #1". Only **Content and file seals** is red. Say: "Only the file changed. The chain and anchors are still green, so it knows exactly what was touched."
6. Open the **Tamper lab** again if it closed and click **Reset demo data**.

# Part 6: Attack 2, the smart attacker

1. In the **Tamper lab**, click **Rewrite the whole chain**.
2. Scroll to **Verify** and click **Verify history**. You will see red "first break at entry #2". **External anchors** and **Rules replay** are red, while Chain links and Entry hashes stay green. Say: "This attacker recomputed every hash, so the chain looks perfect. But every hash was also copied to a separate anchor log, and that catches it."
3. Open the **Tamper lab** and click **Reset demo data**.

# Part 7: Checkpoint (optional)

1. Scroll to the **Checkpoint** card and click **Get checkpoint**, then **Copy** in the green box.
2. Say: "I would email this fingerprint to myself, outside the database. Later I can paste it into Check it to prove nothing was rewritten."

# Close

> "We don't just show who approved. We prove the record was never changed."

# Rules to remember

- **Always click Reset demo data after an attack.** Each one damages the document until you reset. Do it again right before you present.
- **The Tamper lab closes itself** after some actions. Click its red dashed bar to reopen it.
- **If you are short on time**, do Parts 4 and 5 only. That is the core.
- **If a judge asks for extra:** click **Audit all** above the document list to verify every document at once, or **Download audit pack** in the Verify card. Offline check: `python tools/verify_bundle.py pack.json --files data/files`.

# Say the limits yourself before they ask

- A user dropdown stands in for real login.
- The anchor log is a local file in this demo; in production it would live on storage the database owner cannot edit.
- Tamper-evidence detects and locates changes; it does not stop someone with database access from making them.

# Quick answers to likely questions

- **Is this blockchain?** No. It uses the same tamper-evidence idea (hash chaining) with no network or mining, so it is fast and simple.
- **Can't an admin recompute all the hashes?** Yes, which is why every hash is also copied to the external anchor log.
- **What if they delete the last entry?** The chain alone cannot see it, but the anchor log remembers it existed.
- **Why not just use audit logging?** Ordinary audit logs can be edited by whoever controls the database. Ours proves whether they were.
