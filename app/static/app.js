"use strict";
/* SealTrail UI: vanilla JS, no build step. */

const S = {
  users: [], templates: {}, docs: [], sel: null, view: null, at: null,
  verify: {}, verifyAll: {}, fileBad: {}, me: null, demo: false, newStages: [],
};

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => "₹" + new Intl.NumberFormat("en-IN").format(n || 0);
const short = (h) => (h ? h.slice(0, 8) + "…" + h.slice(-4) : "");
const when = (iso) => (iso ? new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "");
function ago(iso) {
  if (!iso) return "";
  const m = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return Math.round(m) + " min ago";
  if (m < 60 * 24) return Math.round(m / 60) + " h ago";
  return Math.round(m / 1440) + " d ago";
}
function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}

/* Upload a file (raw body); returns {filename, sha256, size} to embed in the sealed document content. */
async function uploadFile(file) {
  if (!file) return null;
  const res = await fetch("/api/uploads?filename=" + encodeURIComponent(file.name), { method: "POST", body: file, headers: { "Content-Type": "application/octet-stream" } });
  let data = null;
  try { data = await res.json(); } catch (e) { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || res.statusText);
  return data;
}
const fmtSize = (n) => (n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(1) + " KB" : (n / 1048576).toFixed(1) + " MB");
const FILE_TYPES = ".pdf,.docx,.xlsx,.pptx,.png,.jpg,.jpeg,.txt";
function fileAltered(v) {
  const r = S.verify[v.id];
  return !!S.fileBad[v.id + ":" + v.state.version] || !!(r && r.breaks.some((b) => b.code === "FILE_EDITED" || b.code === "FILE_MISSING"));
}
function attachHtml(v, c) {
  const a = c.attachment;
  if (!a) return "";
  const bad = fileAltered(v);
  return `<div class="attach ${bad ? "bad" : ""}"><span class="ai" aria-hidden="true">${bad ? "⚠" : "📎"}</span>
    <div class="am"><b>${esc(a.filename)}</b>
      <small>${fmtSize(a.size)} · sha256 <code>${a.sha256.slice(0, 8)}...</code>${bad ? ` · <span class="alt">File altered</span>` : ""}</small></div>
    <button class="btn small" data-act="download" data-ver="${v.state.version}">Download</button></div>`;
}
let toastTimer;
function toast(msg, err = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = "toast"), err ? 5200 : 3200);
}
const guard = (fn) => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message || String(e), true); } };

/* ------------------------------------------------------------------ boot */
async function boot() {
  const [users, templates, cfg] = await Promise.all([api("/api/users"), api("/api/templates"), api("/api/config")]);
  S.users = users; S.templates = templates; S.demo = cfg.demo;
  S.me = store("st_me") && users.some((u) => u.id === store("st_me")) ? store("st_me") : "u_rahul";
  const me = $("#me");
  me.innerHTML = users.map((u) => `<option value="${u.id}">${esc(u.name)} · ${esc(u.title)}</option>`).join("");
  me.value = S.me;
  me.addEventListener("change", () => { S.me = me.value; store("st_me", S.me); renderDoc(); });
  $("#tplsel").innerHTML = Object.entries(templates).map(([k, t]) => `<option value="${k}">${esc(t.label)}</option>`).join("") + `<option value="custom">Custom (build my own)</option>`;
  await loadDocs();
  if (S.docs.length) await selectDoc(S.docs[0].id);
  else $("#content").innerHTML = `<div class="empty">No documents yet. Create one.</div>`;
}

async function loadDocs() { S.docs = await api("/api/documents"); renderSidebar(); }

async function selectDoc(id, at = null) {
  S.sel = id; S.at = at;
  S.view = await api(`/api/documents/${encodeURIComponent(id)}` + (at !== null ? `?at=${at}` : ""));
  renderSidebar(); renderDoc();
}

/* ------------------------------------------------------------------ sidebar */
function chainBadge(id) {
  const r = S.verify[id] || S.verifyAll[id];
  if (!r) return "";
  return r.ok ? `<div class="chain" style="color:var(--ok)">✔ History verified</div>`
    : `<div class="chain" style="color:var(--bad)">✖ Tampering detected at #${r.first_break ?? "?"}</div>`;
}
function renderSidebar() {
  $("#doclist").innerHTML = S.docs.map((d) => `
    <button class="doc s-${d.status} ${d.id === S.sel ? "sel" : ""}" data-doc="${esc(d.id)}">
      <div class="r1"><span class="id">${esc(d.id)}</span><span class="pill ${d.status}">${d.status.replace("_", " ")}</span></div>
      <div class="t">${esc(d.title)}</div>
      <div class="m">${inr(d.amount)} · v${d.version} · ${esc(d.owner)}</div>
      <div class="m">${d.status === "IN_PROGRESS" ? `Stage ${d.stage_no}/${d.stage_count}: waiting on ${esc(d.waiting_on.join(", "))}` : d.status === "REJECTED" ? "Waiting for requester to resubmit" : "All stages complete"}</div>
      ${chainBadge(d.id)}
    </button>`).join("");
}

/* ------------------------------------------------------------------ main view */
const MODE_LABEL = (s) => s.mode === "SEQUENTIAL" ? "Sequential · in order" : s.rule === "ANY" ? "Parallel · any one approves" : "Parallel · all must approve";
const ICON = { APPROVED: "✓", REJECTED: "✕", WAITING: "●", QUEUED: "○", NOT_NEEDED: "–" };

function pipelineHtml(v) {
  if (!v.pipeline.length) return `<p class="note">Not submitted.</p>`;
  return `<div class="pipe">` + v.pipeline.map((s, i) => `
    ${i ? `<div class="arrow" aria-hidden="true">➜</div>` : ""}
    <div class="stage ${s.status}">
      <div class="sh"><div><b>${i + 1}. ${esc(s.name)}</b><small>${MODE_LABEL(s)}</small></div><span class="pill ${s.status}">${s.status}</span></div>
      <ul class="appr">${s.approvers.map((a, j) => `
        <li class="${a.state}"><span class="ic">${s.mode === "SEQUENTIAL" && a.state === "QUEUED" ? j + 1 : ICON[a.state]}</span>
          <div>${esc(a.name)}<small>${esc(a.title)}${a.ts ? " · " + when(a.ts) : ""}${a.state === "WAITING" ? " · can act now" : ""}</small></div></li>`).join("")}
      </ul>
    </div>`).join("") + `</div>`;
}

function whyHtml(v) {
  const w = v.why;
  const since = w.since && v.state.status === "IN_PROGRESS" ? `Waiting for ${ago(w.since).replace(" ago", "")} at this stage.` : "";
  return `<div class="why ${w.tone}"><div class="h">${esc(w.headline)}</div><div class="d">${esc(w.detail)}</div>${since ? `<div class="s">${since}</div>` : ""}</div>`;
}

function whyCannot(v) {
  if (v.owner && v.owner.id === S.me) return "You are the requester, so you can't approve your own document (segregation of duties).";
  for (const s of v.pipeline) {
    const a = s.approvers.find((x) => x.id === S.me);
    if (!a) continue;
    if (a.state === "APPROVED") return `You already approved this document at stage ${s.index + 1}.`;
    if (a.state === "REJECTED") return "You rejected this document.";
    if (a.state === "NOT_NEEDED") return "Your approval is no longer needed for this stage.";
    if (a.state === "QUEUED" && s.status === "ACTIVE") {
      const w = s.approvers.find((x) => x.state === "WAITING");
      return `Not your turn yet: ${w ? w.name : "someone"} must act first (sequential stage).`;
    }
    if (a.state === "QUEUED") return `Your stage ("${s.name}") hasn't started yet.`;
  }
  return "You are not an approver for this document, but you can still comment.";
}

function actionsHtml(v) {
  const st = v.state;
  if (S.at !== null) return `<p class="note">Time-travel view is read-only. Move the slider to the end to act.</p>`;
  const isOwner = v.owner && v.owner.id === S.me;
  let h = "";
  if (st.status === "IN_PROGRESS" && v.waiting_now.includes(S.me)) {
    h += `<p class="note"><b>Your decision is needed.</b> Approvals are sealed to the exact content shown above.</p>
      <textarea id="dec-comment" rows="2" placeholder="Comment (required if you reject)"></textarea>
      <div class="actrow"><button class="btn ok" data-act="approve">Approve</button><button class="btn bad" data-act="reject">Reject</button></div>`;
  } else if (st.status === "IN_PROGRESS") {
    h += `<p class="note">${esc(whyCannot(v))}</p>`;
  } else if (st.status === "REJECTED") {
    if (isOwner) {
      const c = v.content || {};
      h += `<p class="note"><b>Your document was rejected.</b> Revise it and resubmit. This creates v${st.version + 1}, restarts approval from stage 1, and keeps all old history.</p>
        <div class="grid2">
          <label>Title<input id="rs-title" value="${esc(c.title)}"></label>
          <label>Vendor<input id="rs-vendor" value="${esc(c.vendor)}"></label>
          <label>Amount (₹)<input id="rs-amount" type="number" min="0" value="${esc(c.amount)}"></label>
          <label>What did you change?<input id="rs-comment" placeholder="e.g. Added cost centre"></label>
        </div>
        <label style="margin-top:8px">Description<textarea id="rs-desc" rows="2">${esc(c.description)}</textarea></label>
        <label style="margin-top:8px">Replace attachment${c.attachment ? ` (currently ${esc(c.attachment.filename)}; leave empty to keep it)` : " (optional)"}<input id="rs-file" type="file" accept="${FILE_TYPES}"></label>
        <div class="actrow"><button class="btn primary" data-act="resubmit">Resubmit as v${st.version + 1}</button></div>`;
    } else {
      h += `<p class="note">Waiting for the requester (${esc(v.owner ? v.owner.name : "")}) to resubmit a revised version.</p>`;
    }
  } else if (st.status === "APPROVED") {
    h += `<p class="note">Fully approved. No further decisions are possible.</p>`;
  }
  h += `<div class="${h ? "subsec" : ""}"><label>Add a comment (recorded in the ledger)
      <input id="cm-text" placeholder="Visible to everyone, sealed like any other entry"></label>
      <div class="actrow"><button class="btn small" data-act="comment">Post comment</button></div></div>`;
  return h;
}

function replayHtml(v) {
  return `<div class="replay"><span class="muted" style="font-size:13px">Start</span>
    <input type="range" id="replay" min="0" max="${v.total}" value="${v.at}" aria-label="Replay the document state after entry number">
    <span class="muted" style="font-size:13px">Now</span>
    <span class="lbl" id="replay-lbl">${replayLabel(v.at, v.total)}</span></div>`;
}
const replayLabel = (at, total) => at >= total ? "Live: all " + total + " entries" : at === 0 ? "Before anything happened" : `State after entry #${at} of ${total}`;

const CHECKS = [["sequence", "No gaps"], ["chain", "Chain links"], ["entries", "Entry hashes"], ["content", "Content and file seals"], ["anchors", "External anchors"], ["rules", "Rules replay"]];
function verifyHtml(v) {
  const r = S.verify[v.id];
  let h = `<div class="vhead"><div><b>Is this history trustworthy?</b><div class="muted" style="font-size:13px">Recomputes every hash, checks content seals against the ledger, compares with the external anchor log and replays the approval rules.</div></div>
    <div class="toolrow"><button class="btn primary" data-act="verify">Verify history</button><button class="btn" data-act="export">Download audit pack</button></div></div>`;
  if (r) {
    const chips = CHECKS.map(([k, l]) => `<span class="chk ${r.checks[k] ? "" : "no"}">${r.checks[k] ? "✓" : "✗"} ${l}</span>`).join("");
    if (r.ok) {
      h += `<div class="vres ok"><div class="vt">✔ HISTORY VERIFIED</div>
        <div>${r.entries} entries, chain intact, ${r.anchored}/${r.entries} sealed in the external anchor log. Head hash <code>${short(r.head)}</code></div><div class="checks">${chips}</div></div>`;
    } else {
      h += `<div class="vres bad"><div class="vt">✖ TAMPERING DETECTED${r.first_break ? ` — first break at entry #${r.first_break}` : ""}</div>
        <ul class="vlist">${r.breaks.map((b) => `<li>${esc(b.message)}</li>`).join("")}</ul><div class="checks">${chips}</div></div>`;
    }
  }
  return h;
}

function ledgerHtml(v) {
  const r = S.verify[v.id];
  const rows = v.entries.map((e) => ({ kind: "entry", seq: e.seq, e }));
  if (r) {
    const have = new Set(v.entries.map((e) => e.seq));
    const ghosts = new Set(r.breaks.filter((b) => b.seq && !have.has(b.seq)).map((b) => b.seq));
    ghosts.forEach((s) => rows.push({ kind: "ghost", seq: s }));
  }
  rows.sort((a, b) => a.seq - b.seq);
  const stageName = (i) => (v.pipeline[i] ? v.pipeline[i].name : null);
  return `<div class="tl">` + rows.map((row) => {
    const bs = r ? r.breaks.filter((b) => b.seq === row.seq) : [];
    if (row.kind === "ghost") {
      return `<div class="entry ghost bad"><div class="rail"><span class="dot"></span></div><div class="body">
        <div class="l1"><span class="badge REJECT">MISSING</span><b>Entry #${row.seq}</b><span class="muted">no longer in the database</span></div>
        ${bs.map((b) => `<div class="why-bad">${esc(b.message)}</div>`).join("")}</div></div>`;
    }
    const e = row.e;
    const cls = (r ? r.status[e.seq] || "ok" : "") + (e.future ? " future" : "");
    const seal = e.payload && e.payload.content_hash ? `<span>${e.action === "APPROVE" ? "approves content seal" : "content seal"}</span><code>${short(e.payload.content_hash)}</code>` : "";
    const ver = v.versions.find((x) => x.version === e.version);
    const att = (e.action === "SUBMIT" || e.action === "RESUBMIT") && ver && ver.content && ver.content.attachment;
    const attLine = att ? `<div class="cm muted">attachment: ${esc(att.filename)} <code>${att.sha256.slice(0, 8)}...</code></div>` : "";
    const changed = e.payload && e.payload.changed ? `<div class="cm muted">Changed: ${e.payload.changed.map(esc).join(", ")}</div>` : "";
    const where = e.action === "SUBMIT" || e.action === "RESUBMIT" ? "" : ` · Stage ${e.stage_idx + 1}${stageName(e.stage_idx) ? ": " + esc(stageName(e.stage_idx)) : ""}`;
    return `<div class="entry ${cls}"><div class="rail"><span class="dot"></span></div><div class="body">
      <div class="l1"><span class="badge ${e.action}">${e.action}</span><b>${esc(e.actor_name)}</b><span class="muted" style="font-size:12.5px">${esc(e.actor_title)} · v${e.version}${where}</span><span class="tm">#${e.seq} · ${when(e.ts)}</span></div>
      ${e.comment ? `<p class="cm">“${esc(e.comment)}”</p>` : ""}${changed}${attLine}
      <div class="hs"><span>prev</span><code>${e.seq === 1 ? "genesis" : short(e.prev_hash)}</code><span>→ hash</span><code>${short(e.hash)}</code>${seal}</div>
      ${bs.map((b) => `<div class="why-bad">${esc(b.message)}</div>`).join("")}
      ${r && r.status[e.seq] === "untrusted" ? `<div class="why-bad">Untrusted: it sits after a broken entry, so the chain can no longer vouch for it.</div>` : ""}
    </div></div>`;
  }).join("") + `</div>`;
}

function checkpointHtml() {
  return `<div class="vhead"><div><b>Checkpoint: your own copy of the anchor</b><div class="muted" style="font-size:13px">A short fingerprint of ALL history so far. Paste it into an email or chat: that copy lives outside this database, so nobody with database access can edit it.</div></div>
    <div class="toolrow"><button class="btn primary" data-act="checkpoint">Get checkpoint</button></div></div>
    <div id="cp-out"></div>
    <label style="margin-top:10px">Check a saved checkpoint against the current history
      <input id="cp-in" placeholder="Paste a checkpoint (or just its 64-character anchor head)"></label>
    <div class="actrow"><button class="btn small" data-act="cp-check">Check it</button></div><div id="cp-res"></div>`;
}

const LAB = [
  ["edit_actor", "Change who approved", "Edit the approver name on one approval, like swapping the blame."],
  ["edit_comment", "Rewrite a comment", "Replace a rejection reason with “Looks fine to me.”"],
  ["edit_content", "Lower the amount", "Change the approved amount to a tenth of its value."],
  ["swap_file", "Swap the uploaded file", "Overwrite the stored attachment (e.g. the vendor quote) after approval."],
  ["delete_entry", "Delete an entry", "Remove a rejection or approval from history."],
  ["forge_approval", "Forge an approval", "Inject a valid-looking APPROVE that never went through the app."],
  ["rewrite_chain", "Rewrite the whole chain", "Smart attacker: edit an entry and recompute every hash after it."],
];
function labHtml(v) {
  if (!S.demo) return "";
  return `<details class="card lab"><summary>⚠ Tamper lab (demo): play the insider attacker</summary>
    <p class="note" style="margin-top:8px">These buttons edit the database directly with raw SQL, bypassing the app. After tampering, click <b>Verify history</b> above. Use <b>Reset demo data</b> to start over.</p>
    <div class="labgrid">${LAB.map(([m, t, d]) => `<button class="labbtn" data-act="tamper" data-mode="${m}"><b>${t}</b><span>${d}</span></button>`).join("")}
      <button class="labbtn" data-act="reset"><b>Reset demo data</b><span>Restore the four seeded purchase orders.</span></button></div></details>`;
}

function renderDoc() {
  const v = S.view;
  if (!v) return;
  const c = v.content || {};
  const st = v.state;
  const errBanner = st.error ? `<div class="banner bad"><b>Replay stopped at entry #${st.error.seq}.</b> ${esc(st.error.message)}. The state shown is the last one the ledger can vouch for.</div>` : "";
  const trav = S.at !== null ? `<div class="banner warn">Time-travel view: showing the document as it was after entry #${S.at}. Entries after that are dimmed.</div>` : "";
  $("#content").innerHTML = `
    ${errBanner}${trav}
    <div class="card">
      <div class="dochead">
        <div><div class="muted mono">${esc(v.id)}</div><h2>${esc(c.title || "(unreadable)")} <span class="ver">v${st.version}</span></h2>
          <div class="facts"><span>Requester <b>${esc(v.owner ? v.owner.name : "")}</b></span><span>Vendor <b>${esc(c.vendor || "n/a")}</b></span></div></div>
        <div style="text-align:right"><div class="amount">${inr(c.amount)}</div><span class="pill ${st.status}">${st.status.replace("_", " ")}</span></div>
      </div>
      ${c.description ? `<p class="desc">${esc(c.description)}</p>` : ""}
      ${attachHtml(v, c)}
    </div>
    <div class="card"><h3>Approval pipeline</h3>${pipelineHtml(v)}</div>
    <div class="card"><h3>Why is it stuck?</h3>${whyHtml(v)}</div>
    <div class="card actbox"><h3>Your actions</h3>${actionsHtml(v)}</div>
    <div class="card"><h3>Time travel</h3>${replayHtml(v)}</div>
    <div class="card" id="verify-card"><h3>Verify</h3>${verifyHtml(v)}</div>
    <div class="card"><h3>Checkpoint</h3>${checkpointHtml()}</div>
    <div class="card"><h3>Approval ledger (hash-chained)</h3>${ledgerHtml(v)}</div>
    ${labHtml(v)}`;
}

/* ------------------------------------------------------------------ actions */
async function refreshAfterWrite(view) {
  delete S.verify[S.sel]; delete S.verifyAll[S.sel]; S.fileBad = {};
  S.at = null; S.view = view;
  await loadDocs();
  renderDoc();
}

const doAction = guard(async (action, extra = {}) => {
  const res = await api(`/api/documents/${encodeURIComponent(S.sel)}/actions`, { method: "POST", body: { actor: S.me, action, ...extra } });
  await refreshAfterWrite(res.view);
  toast(`${action} sealed as entry #${res.entry.seq} (hash ${short(res.entry.hash)})`);
});

const doDownload = guard(async (ver) => {
  const res = await fetch(`/api/documents/${encodeURIComponent(S.sel)}/file?version=${ver}`);
  if (res.status === 409) {
    let d = "The attached file no longer matches its sealed hash.";
    try { d = (await res.json()).detail || d; } catch (e) { /* keep default */ }
    S.fileBad[S.sel + ":" + ver] = true; renderDoc();
    return toast(d, true);
  }
  if (!res.ok) throw new Error("Download failed (" + res.status + ").");
  const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a"); a.href = url; a.download = m ? m[1] : "attachment"; document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
  toast("File downloaded. Its bytes matched the sealed hash.");
});

const doVerify = guard(async () => {
  const r = await api(`/api/documents/${encodeURIComponent(S.sel)}/verify`);
  S.verify[S.sel] = r;
  renderSidebar(); renderDoc();
  const card = $("#verify-card"); if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
  toast(r.ok ? "History verified: nothing was altered." : "Tampering detected!", !r.ok);
});

const doVerifyAll = guard(async () => {
  const r = await api("/api/verify");
  S.verifyAll = {}; r.docs.forEach((d) => (S.verifyAll[d.doc_id] = d));
  const bad = r.docs.filter((d) => !d.ok).map((d) => d.doc_id);
  renderSidebar();
  toast(bad.length ? `Audit: ${bad.length} tampered (${bad.join(", ")}), ${r.docs.length - bad.length} intact.` : `Audit: all ${r.docs.length} documents intact.`, bad.length > 0);
});

const doCheckpoint = guard(async () => {
  const c = await api("/api/checkpoint");
  const text = `SealTrail checkpoint ${c.ts} | ${c.entries} anchored entries | anchor head ${c.anchor_head}`;
  $("#cp-out").innerHTML = `<div class="vres ${c.log_ok ? "ok" : "bad"}"><div class="vt">${c.log_ok ? "Checkpoint ready" : "Anchor log is already broken"}</div>
    <code style="word-break:break-all;display:block;margin:6px 0">${esc(text)}</code>
    <button class="btn small" id="cp-copy">Copy</button></div>`;
  $("#cp-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(text); toast("Checkpoint copied. Paste it somewhere outside this system."); }
    catch (e) { const r = document.createRange(); r.selectNodeContents($("#cp-out code")); getSelection().removeAllRanges(); getSelection().addRange(r); toast("Selected. Press Ctrl+C to copy."); }
  };
});

const doCheckpointCheck = guard(async () => {
  const m = ($("#cp-in").value.match(/[0-9a-f]{64}/i) || [""])[0];
  if (!m) return toast("Paste a checkpoint containing a 64-character anchor head.", true);
  const r = await api("/api/checkpoint/check", { method: "POST", body: { anchor: m } });
  $("#cp-res").innerHTML = `<div class="vres ${r.found ? "ok" : "bad"}"><div class="vt">${r.found ? "✔ Checkpoint matches" : "✖ Checkpoint does not match"}</div><div>${esc(r.message)}</div></div>`;
});

const doTamper = guard(async (mode) => {
  const r = await api("/api/demo/tamper", { method: "POST", body: { doc_id: S.sel, mode } });
  await refreshAfterWrite(r.view);
  toast(r.message + " Now click Verify history.");
});

const doReset = guard(async () => {
  await api("/api/demo/reset", { method: "POST" });
  S.verify = {}; S.verifyAll = {};
  await loadDocs();
  await selectDoc(S.sel && S.docs.some((d) => d.id === S.sel) ? S.sel : S.docs[0].id);
  toast("Demo data reset.");
});

/* ------------------------------------------------------------------ new-document dialog */
function stageFromTemplate(key) {
  const t = S.templates[key];
  return t ? t.stages.map((s) => ({ ...s, approvers: s.approvers.filter((a) => a !== S.me) })) : S.newStages;
}
function renderStages() {
  $("#stagerows").innerHTML = S.newStages.map((s, i) => `
    <div class="stagerow" data-i="${i}">
      <label>Stage name<input data-f="name" value="${esc(s.name)}"></label>
      <label>Mode<select data-f="mode"><option value="SEQUENTIAL" ${s.mode === "SEQUENTIAL" ? "selected" : ""}>Sequential (in order)</option><option value="PARALLEL" ${s.mode === "PARALLEL" ? "selected" : ""}>Parallel (any order)</option></select></label>
      <label>Rule<select data-f="rule" ${s.mode === "SEQUENTIAL" ? "disabled" : ""}><option value="ALL" ${s.rule === "ALL" ? "selected" : ""}>All must approve</option><option value="ANY" ${s.rule === "ANY" ? "selected" : ""}>Any one</option></select></label>
      <button type="button" class="btn small" data-act="rm-stage" data-i="${i}" ${S.newStages.length < 2 ? "disabled" : ""} aria-label="Remove stage">✕</button>
      <div class="ap">${S.users.filter((u) => u.id !== S.me).map((u) => `<label><input type="checkbox" data-f="ap" value="${u.id}" ${s.approvers.includes(u.id) ? "checked" : ""}>${esc(u.name)} <span class="muted">(${esc(u.title)})</span></label>`).join("")}</div>
    </div>`).join("");
}
function readStages() {
  const rows = [...document.querySelectorAll("#stagerows .stagerow")];
  S.newStages = rows.map((r) => {
    const mode = r.querySelector('[data-f="mode"]').value;
    return {
      name: r.querySelector('[data-f="name"]').value, mode,
      rule: mode === "SEQUENTIAL" ? "ALL" : r.querySelector('[data-f="rule"]').value,
      approvers: [...r.querySelectorAll('[data-f="ap"]:checked')].map((c) => c.value),
    };
  });
}
function openNew() {
  $("#newform").reset();
  $("#tplsel").value = "standard";
  S.newStages = stageFromTemplate("standard");
  renderStages();
  $("#newdlg").showModal();
}

/* ------------------------------------------------------------------ events */
document.addEventListener("click", (ev) => {
  const docBtn = ev.target.closest("[data-doc]");
  if (docBtn) return void guard(selectDoc)(docBtn.dataset.doc);
  const b = ev.target.closest("[data-act]");
  if (!b) return;
  const a = b.dataset.act;
  const val = (id) => ($(id) ? $(id).value.trim() : "");
  if (a === "new") openNew();
  else if (a === "cancel-new") $("#newdlg").close();
  else if (a === "add-stage") { readStages(); S.newStages.push({ name: `Stage ${S.newStages.length + 1}`, mode: "PARALLEL", rule: "ALL", approvers: [] }); $("#tplsel").value = "custom"; renderStages(); }
  else if (a === "rm-stage") { readStages(); S.newStages.splice(+b.dataset.i, 1); $("#tplsel").value = "custom"; renderStages(); }
  else if (a === "verify-all") doVerifyAll();
  else if (a === "verify") doVerify();
  else if (a === "download") doDownload(b.dataset.ver);
  else if (a === "checkpoint") doCheckpoint();
  else if (a === "cp-check") doCheckpointCheck();
  else if (a === "export") window.location.href = `/api/documents/${encodeURIComponent(S.sel)}/export`;
  else if (a === "approve") doAction("APPROVE", { comment: val("#dec-comment") });
  else if (a === "reject") {
    if (!val("#dec-comment")) return toast("Please write a reason. A rejection must include a comment.", true);
    doAction("REJECT", { comment: val("#dec-comment") });
  } else if (a === "comment") {
    if (!val("#cm-text")) return toast("Write a comment first.", true);
    doAction("COMMENT", { comment: val("#cm-text") });
  } else if (a === "resubmit") {
    guard(async () => {
      const up = await uploadFile($("#rs-file") && $("#rs-file").files[0]);
      const attachment = up || (S.view.content && S.view.content.attachment) || undefined;
      await doAction("RESUBMIT", { comment: val("#rs-comment"), content: { title: val("#rs-title"), vendor: val("#rs-vendor"), amount: val("#rs-amount"), description: val("#rs-desc"), attachment } });
    })();
  } else if (a === "tamper") doTamper(b.dataset.mode);
  else if (a === "reset") doReset();
});

document.addEventListener("input", (ev) => {
  if (ev.target.id === "replay") { $("#replay-lbl").textContent = replayLabel(+ev.target.value, S.view.total); }
});
document.addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.id === "replay") { const n = +t.value; guard(selectDoc)(S.sel, n >= S.view.total ? null : n); }
  else if (t.id === "tplsel") { if (t.value !== "custom") { S.newStages = stageFromTemplate(t.value); renderStages(); } }
  else if (t.closest && t.closest("#stagerows")) { readStages(); if (t.dataset.f === "mode") renderStages(); $("#tplsel").value = "custom"; }
});

$("#newform").addEventListener("submit", guard(async (ev) => {
  ev.preventDefault();
  readStages();
  const f = new FormData($("#newform"));
  const attachment = await uploadFile($("#newfile").files[0]) || undefined;
  const res = await api("/api/documents", {
    method: "POST",
    body: { owner: S.me, content: { title: f.get("title"), vendor: f.get("vendor"), amount: f.get("amount"), description: f.get("description"), attachment }, stages: S.newStages },
  });
  $("#newdlg").close();
  await loadDocs();
  await selectDoc(res.id);
  toast(`${res.id} submitted and sealed as entry #1.`);
}));

boot().catch((e) => { $("#content").innerHTML = `<div class="empty">Could not load: ${esc(e.message)}</div>`; });
