"""FastAPI app: thin HTTP layer over workflow.py and ledger.py."""
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, files, seed, tamper
from . import ledger as L
from . import workflow as W

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEMO = os.environ.get("SEALTRAIL_DEMO", "1") == "1"


def _fresh_seed():
    db.reset_all()
    conn = db.connect()
    try:
        seed.seed(conn)
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    conn = db.connect()
    try:
        empty = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
    finally:
        conn.close()
    if empty:
        _fresh_seed()
    yield


app = FastAPI(title="SealTrail: tamper-evident approvals", lifespan=lifespan)


@app.exception_handler(W.PolicyError)
async def _policy(_, exc):
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(W.NotFound)
async def _nf(_, exc):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


class ActionIn(BaseModel):
    actor: str
    action: str
    comment: str = ""
    content: Optional[dict] = None


class CreateIn(BaseModel):
    owner: str
    content: dict
    stages: Optional[list] = None
    template: Optional[str] = None


class CheckpointIn(BaseModel):
    anchor: str


class TamperIn(BaseModel):
    doc_id: str
    mode: str


def _db():
    return db.connect()


# ------------------------------------------------------------------ reads
@app.get("/api/config")
def config():
    return {"demo": DEMO}


@app.get("/api/users")
def users():
    conn = _db()
    try:
        return list(W.get_users(conn).values())
    finally:
        conn.close()


@app.get("/api/templates")
def templates():
    return W.TEMPLATES


@app.get("/api/documents")
def documents():
    conn = _db()
    try:
        return W.list_documents(conn)
    finally:
        conn.close()


@app.get("/api/documents/{doc_id}")
def document(doc_id: str, at: Optional[int] = Query(None)):
    conn = _db()
    try:
        return W.document_view(conn, doc_id, at)
    finally:
        conn.close()


@app.get("/api/documents/{doc_id}/verify")
def verify_one(doc_id: str):
    conn = _db()
    try:
        if not L.load_entries(conn, doc_id) and not conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone():
            raise W.NotFound(f"Document {doc_id} not found.")
        return L.verify(conn, doc_id)
    finally:
        conn.close()


@app.get("/api/verify")
def verify_all():
    conn = _db()
    try:
        lines = L.read_anchor_lines()
        # Audit every document the database lists AND every document the external anchor log remembers,
        # so a document deleted outright cannot silently vanish from the audit.
        ids = {r["id"] for r in conn.execute("SELECT id FROM documents").fetchall()}
        ids |= {a["doc_id"] for a in lines if a.get("doc_id")}
        results = [L.verify(conn, d, lines) for d in sorted(ids)]
        return {"ok": all(r["ok"] for r in results), "docs": results}
    finally:
        conn.close()


@app.get("/api/documents/{doc_id}/export")
def export(doc_id: str):
    """Audit pack: everything an outside auditor needs to verify offline (see tools/verify_bundle.py)."""
    conn = _db()
    try:
        entries = L.load_entries(conn, doc_id)
        if not entries:
            raise W.NotFound(f"Document {doc_id} not found.")
        versions = [{"version": v["version"], "content": v["content"], "content_hash": v["content_hash"],
                     "created_at": v["created_at"]} for v in W._load_versions(conn, doc_id)]
        bundle = {
            "format": "sealtrail-bundle/1", "doc_id": doc_id, "exported_at": L.now_iso(),
            "versions": versions, "ledger": entries,
            "anchors": [a for a in L.read_anchor_lines() if a.get("doc_id") == doc_id],
            "verification": L.verify(conn, doc_id),
        }
        return JSONResponse(bundle, headers={"Content-Disposition": f'attachment; filename="{doc_id}-audit-pack.json"'})
    finally:
        conn.close()


@app.get("/api/checkpoint")
def checkpoint():
    return L.checkpoint()


@app.post("/api/checkpoint/check")
def checkpoint_check(body: CheckpointIn):
    return L.check_checkpoint(body.anchor)


@app.get("/api/documents/{doc_id}/versions/{version}/attachment")
def attachment(doc_id: str, version: int):
    """Download the file exactly as stored (verification, not this route, says whether it is trustworthy)."""
    conn = _db()
    try:
        v = next((x for x in W._load_versions(conn, doc_id) if x["version"] == version), None)
        att = ((v or {}).get("content") or {}).get("attachment")
        if not att:
            raise W.NotFound("No attachment on that version.")
        try:
            with open(files.path_for(att["sha256"]), "rb") as f:
                data = f.read()
        except (OSError, ValueError):
            raise W.NotFound("The stored file is missing.")
        name = files.clean_name(att.get("filename")).replace('"', "")
        return Response(data, media_type="application/octet-stream",
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "X-Content-Type-Options": "nosniff"})
    finally:
        conn.close()


# ------------------------------------------------------------------ writes
@app.post("/api/files")
async def upload(request: Request, filename: str = Query("attachment")):
    """Raw-body upload (no multipart dependency). Returns the reference to put inside document content."""
    data = await request.body()
    try:
        return files.store(data, filename)
    except ValueError as ex:
        raise W.PolicyError(str(ex))

@app.post("/api/documents")
def create(body: CreateIn):
    conn = _db()
    try:
        stages = body.stages
        if not stages and body.template:
            if body.template not in W.TEMPLATES:
                raise W.PolicyError("Unknown template.")
            stages = W.TEMPLATES[body.template]["stages"]
        doc_id = W.create_document(conn, body.owner, body.content, stages)
        return {"id": doc_id, "view": W.document_view(conn, doc_id)}
    finally:
        conn.close()


@app.post("/api/documents/{doc_id}/actions")
def act(doc_id: str, body: ActionIn):
    conn = _db()
    try:
        e = W.perform(conn, doc_id, body.actor, body.action, body.comment, body.content)
        return {"entry": {"seq": e["seq"], "hash": e["hash"], "action": e["action"]},
                "view": W.document_view(conn, doc_id)}
    finally:
        conn.close()


# ------------------------------------------------------------------ demo only
def _demo_guard():
    if not DEMO:
        raise HTTPException(status_code=403, detail="Demo tools are disabled (SEALTRAIL_DEMO=0).")


@app.post("/api/demo/tamper")
def demo_tamper(body: TamperIn):
    _demo_guard()
    conn = _db()
    try:
        res = tamper.tamper(conn, body.doc_id, body.mode)
        return {**res, "view": W.document_view(conn, body.doc_id)}
    finally:
        conn.close()


@app.post("/api/demo/reset")
def demo_reset():
    _demo_guard()
    _fresh_seed()
    return {"ok": True}


# ------------------------------------------------------------------ static UI
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
