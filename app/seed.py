"""Demo data: a small company and four purchase-order stories."""
import datetime as dt

from . import files
from . import workflow as W

USERS = [
    ("u_aarav", "Aarav Kumar", "REQUESTER", "Engineering, Requester"),
    ("u_priya", "Priya Nair", "REQUESTER", "Operations, Requester"),
    ("u_meera", "Meera Iyer", "MANAGER", "Engineering Manager"),
    ("u_rahul", "Rahul Menon", "FINANCE", "Finance Head"),
    ("u_sneha", "Sneha Rao", "LEGAL", "Legal Counsel"),
    ("u_karthik", "Karthik S", "PROCUREMENT", "Procurement Lead"),
    ("u_divya", "Divya Shah", "HR", "HR Business Partner"),
    ("u_ishaan", "Ishaan Verma", "AUDITOR", "Internal Auditor"),
]


def make_pdf(title: str, lines: list) -> bytes:
    """A tiny valid one-page PDF, hand-built so the demo needs no PDF library."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    body = ["BT /F1 18 Tf 60 760 Td (%s) Tj ET" % esc(title)]
    y = 730
    for ln in lines:
        body.append("BT /F1 12 Tf 60 %d Td (%s) Tj ET" % (y, esc(ln)))
        y -= 20
    stream = "\n".join(body).encode("latin-1")
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def seed(conn):
    conn.executemany("INSERT OR REPLACE INTO users VALUES(?,?,?,?)", USERS)
    base = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) - dt.timedelta(days=6)
    clock = {"t": base}

    def tick(hours=3.0):
        clock["t"] += dt.timedelta(hours=hours)
        return clock["t"].isoformat(timespec="milliseconds")

    std = W.TEMPLATES["standard"]["stages"]

    # DOC-001: fully approved (the "everything is fine" story used for the tamper demo)
    quote = files.store(make_pdf("Dell India Pvt Ltd - Quotation Q-2291", [
        "10 x Latitude 5440 (16 GB RAM, 512 GB SSD)", "Unit price: INR 50,000", "Total: INR 500,000 (excl. GST)",
        "Valid for 30 days."]), "Dell-quote-Q2291.pdf")
    d1 = W.create_document(conn, "u_aarav", {
        "title": "Laptops for 10 new hires", "vendor": "Dell India Pvt Ltd", "amount": 500000,
        "description": "10 developer laptops (16 GB RAM, 512 GB SSD) for the July joining batch.",
        "attachment": quote}, std, ts=tick(0))
    W.perform(conn, d1, "u_meera", "APPROVE", "Within team hardware budget.", ts=tick(4))
    W.perform(conn, d1, "u_rahul", "APPROVE", "Budget code ENG-HW-24 confirmed.", ts=tick(5))
    W.perform(conn, d1, "u_sneha", "APPROVE", "Vendor contract terms are standard.", ts=tick(6))
    W.perform(conn, d1, "u_karthik", "APPROVE", "Best of three quotes.", ts=tick(2))

    # DOC-002: stuck at a parallel stage (the "why is it stuck?" story)
    clock["t"] = base + dt.timedelta(days=1)
    d2 = W.create_document(conn, "u_priya", {
        "title": "Office renovation contract", "vendor": "BuildRight Interiors", "amount": 1200000,
        "description": "Floor 3 renovation: partitions, lighting and flooring. 6-week schedule."}, std, ts=tick(0))
    W.perform(conn, d2, "u_meera", "APPROVE", "Needed before the audit visit.", ts=tick(5))
    W.perform(conn, d2, "u_rahul", "APPROVE", "Approved. Paid in 3 milestones.", ts=tick(4))
    W.perform(conn, d2, "u_sneha", "APPROVE", "Penalty clause added.", ts=tick(9))

    # DOC-003: rejected, revised and resubmitted (v1 -> v2)
    clock["t"] = base + dt.timedelta(days=2)
    hosting_v1 = files.store(make_pdf("CloudNimbus - Renewal quote (v1)", [
        "Annual production hosting renewal", "Total: INR 240,000", "Includes a 20% price increase."]), "CloudNimbus-renewal-v1.pdf")
    hosting_v2 = files.store(make_pdf("CloudNimbus - Renewal quote (revised)", [
        "Annual production hosting renewal", "Total: INR 216,000", "Price increase reduced to 8%.", "Cost centre ENG-INFRA-07."]),
        "CloudNimbus-renewal-v2-revised.pdf")
    d3 = W.create_document(conn, "u_aarav", {
        "title": "Cloud hosting renewal", "vendor": "CloudNimbus", "amount": 240000,
        "description": "Annual renewal of production hosting.", "attachment": hosting_v1}, std, ts=tick(0))
    W.perform(conn, d3, "u_meera", "APPROVE", "Renewal is routine.", ts=tick(3))
    W.perform(conn, d3, "u_rahul", "REJECT", "Cost centre is missing. Attach it and justify the 20% price increase.", ts=tick(6))
    W.perform(conn, d3, "u_aarav", "COMMENT", "Understood, will revise today.", ts=tick(1))
    W.perform(conn, d3, "u_aarav", "RESUBMIT", "Added cost centre ENG-INFRA-07 and negotiated the increase down.", content={
        "title": "Cloud hosting renewal", "vendor": "CloudNimbus", "amount": 216000,
        "description": "Annual renewal of production hosting. Cost centre ENG-INFRA-07. Price increase reduced from 20% to 8%.",
        "attachment": hosting_v2},
        ts=tick(5))
    W.perform(conn, d3, "u_meera", "APPROVE", "Thanks for fixing the numbers.", ts=tick(3))

    # DOC-004: a single "any one of" stage, untouched
    clock["t"] = base + dt.timedelta(days=5)
    W.create_document(conn, "u_priya", {
        "title": "Team offsite venue booking", "vendor": "Lakeview Resorts", "amount": 80000,
        "description": "Two-day offsite for 25 people."}, W.TEMPLATES["quick"]["stages"], ts=tick(0))
