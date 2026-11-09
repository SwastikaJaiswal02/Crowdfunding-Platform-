#!/usr/bin/env python3
"""
College Crowdfunding Platform - backend
---------------------------------------
Pure Python standard library (http.server + sqlite3). No packages to install.

Run:   python3 server.py            -> http://localhost:8000
Env:   PORT (default 8000), DB_PATH (default ./crowdfunding.db), NO_SEED=1

Roles
  student   - creates projects, defines milestones, marks them done, posts updates
  teacher   - approves/rejects projects, verifies milestones (this drives the real % done)
  customer  - funds or buys projects, monitors progress, comments, suggests ideas

NOTE: payments are SIMULATED (no real money moves). Plug a real gateway
(Razorpay / Stripe) into `contribute()` before using this in production.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "crowdfunding.db"))
PORT = int(os.environ.get("PORT", "8000"))
SESSION_TTL = 60 * 60 * 24 * 7  # 7 days
MAX_BODY = 1_000_000
ROLES = ("student", "teacher", "customer")
CATEGORIES = ("Web", "Mobile", "AI/ML", "IoT", "Data", "Hardware", "Research", "Other")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
  pw_hash TEXT NOT NULL, salt TEXT NOT NULL,
  role TEXT NOT NULL, college TEXT DEFAULT '',
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
  token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id INTEGER NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
  description TEXT DEFAULT '', category TEXT DEFAULT 'Other',
  price REAL NOT NULL DEFAULT 0, goal REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | completed
  teacher_feedback TEXT DEFAULT '', reviewed_by INTEGER,
  deliverable_url TEXT DEFAULT '', idea_id INTEGER,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS milestones(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL, title TEXT NOT NULL,
  weight INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'todo',      -- todo | done | verified
  done_at INTEGER, verified_at INTEGER, teacher_note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS updates(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  kind TEXT NOT NULL DEFAULT 'update',      -- update | comment | system
  body TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS contributions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  amount REAL NOT NULL, kind TEXT NOT NULL,  -- fund | buy
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ideas(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
  category TEXT DEFAULT 'Other', claimed_by INTEGER, project_id INTEGER,
  created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS idea_votes(
  idea_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  PRIMARY KEY(idea_id, user_id));
"""


# --------------------------------------------------------------------------- helpers
class ApiError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code, self.msg = code, msg


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    db = connect()
    db.executescript(SCHEMA)
    db.commit()
    db.close()


def hash_pw(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()


def now():
    return int(time.time())


def clean(val, name, maxlen=200, required=True):
    val = (val or "").strip() if isinstance(val, str) else ("" if val is None else str(val).strip())
    if required and not val:
        raise ApiError(400, f"{name} is required")
    if len(val) > maxlen:
        raise ApiError(400, f"{name} is too long (max {maxlen})")
    return val


def money(val, name, allow_zero=False):
    try:
        n = round(float(val), 2)
    except (TypeError, ValueError):
        raise ApiError(400, f"{name} must be a number")
    if n < 0 or (n == 0 and not allow_zero) or n > 10_000_000:
        raise ApiError(400, f"{name} must be a positive amount")
    return n


def need_role(ctx, *roles):
    if not ctx["user"]:
        raise ApiError(401, "Please log in")
    if ctx["user"]["role"] not in roles:
        raise ApiError(403, "Your account type is not allowed to do this")
    return ctx["user"]


def public_user(u):
    return {"id": u["id"], "name": u["name"], "email": u["email"], "role": u["role"], "college": u["college"]}


def progress_of(db, pid):
    rows = db.execute("SELECT status, weight FROM milestones WHERE project_id=?", (pid,)).fetchall()
    total = sum(r["weight"] for r in rows)
    if not total:
        return {"verified": 0, "claimed": 0, "milestones": 0}
    verified = sum(r["weight"] for r in rows if r["status"] == "verified")
    claimed = sum(r["weight"] for r in rows if r["status"] in ("done", "verified"))
    return {"verified": round(100 * verified / total), "claimed": round(100 * claimed / total), "milestones": len(rows)}


def project_card(db, p):
    raised = db.execute("SELECT COALESCE(SUM(amount),0) s, COUNT(*) c FROM contributions WHERE project_id=?",
                        (p["id"],)).fetchone()
    owner = db.execute("SELECT name, college FROM users WHERE id=?", (p["student_id"],)).fetchone()
    d = {k: p[k] for k in ("id", "title", "summary", "category", "price", "goal", "status", "created_at", "student_id")}
    d.update(raised=raised["s"], backers=raised["c"], student_name=owner["name"] if owner else "?",
             college=owner["college"] if owner else "", progress=progress_of(db, p["id"]))
    return d


def is_supporter(db, user, pid):
    if not user:
        return False
    return db.execute("SELECT 1 FROM contributions WHERE project_id=? AND user_id=?", (pid, user["id"])).fetchone() is not None


def system_note(db, pid, user_id, body):
    db.execute("INSERT INTO updates(project_id,user_id,kind,body,created_at) VALUES(?,?,?,?,?)",
               (pid, user_id, "system", body, now()))


def load_project(db, pid):
    p = db.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not p:
        raise ApiError(404, "Project not found")
    return p


def own_project(ctx, pid):
    user = need_role(ctx, "student")
    p = load_project(ctx["db"], pid)
    if p["student_id"] != user["id"]:
        raise ApiError(403, "This is not your project")
    return user, p


def refresh_completion(db, pid):
    """When every milestone is teacher-verified the project becomes 'completed'."""
    p = load_project(db, pid)
    pr = progress_of(db, pid)
    if p["status"] == "approved" and pr["milestones"] and pr["verified"] == 100:
        db.execute("UPDATE projects SET status='completed' WHERE id=?", (pid,))
        system_note(db, pid, p["student_id"], "All milestones verified - project marked COMPLETED.")
    elif p["status"] == "completed" and pr["verified"] < 100:
        db.execute("UPDATE projects SET status='approved' WHERE id=?", (pid,))


# --------------------------------------------------------------------------- auth
def register(ctx):
    b, db = ctx["body"], ctx["db"]
    name = clean(b.get("name"), "Name", 80)
    email = clean(b.get("email"), "Email", 120).lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ApiError(400, "Enter a valid email")
    pw = b.get("password") or ""
    if len(pw) < 6:
        raise ApiError(400, "Password must be at least 6 characters")
    role = b.get("role")
    if role not in ROLES:
        raise ApiError(400, "Role must be student, teacher or customer")
    college = clean(b.get("college"), "College", 120, required=False)
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise ApiError(409, "An account with this email already exists")
    salt = secrets.token_hex(16)
    cur = db.execute("INSERT INTO users(name,email,pw_hash,salt,role,college,created_at) VALUES(?,?,?,?,?,?,?)",
                     (name, email, hash_pw(pw, salt), salt, role, college, now()))
    user = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    return 201, {"token": new_session(db, user["id"]), "user": public_user(user)}


def new_session(db, uid):
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)", (token, uid, now()))
    return token


def login(ctx):
    b, db = ctx["body"], ctx["db"]
    email = (b.get("email") or "").strip().lower()
    u = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not u or not hmac.compare_digest(u["pw_hash"], hash_pw(b.get("password") or "", u["salt"])):
        raise ApiError(401, "Wrong email or password")
    return 200, {"token": new_session(db, u["id"]), "user": public_user(u)}


def logout(ctx):
    if ctx["token"]:
        ctx["db"].execute("DELETE FROM sessions WHERE token=?", (ctx["token"],))
    return 200, {"ok": True}


def me(ctx):
    if not ctx["user"]:
        raise ApiError(401, "Not logged in")
    return 200, {"user": public_user(ctx["user"])}


# --------------------------------------------------------------------------- projects
def list_projects(ctx):
    db, user, q = ctx["db"], ctx["user"], ctx["query"]
    sql, args = "SELECT * FROM projects WHERE 1=1", []
    scope = q.get("scope", "public")
    if scope == "mine":
        user = need_role(ctx, "student")
        sql += " AND student_id=?"
        args.append(user["id"])
    elif scope == "pending":
        need_role(ctx, "teacher")
        sql += " AND status='pending'"
    elif scope == "supported":
        user = need_role(ctx, "customer")
        sql += " AND id IN (SELECT project_id FROM contributions WHERE user_id=?)"
        args.append(user["id"])
    elif scope == "reviewed":
        user = need_role(ctx, "teacher")
        sql += " AND reviewed_by=?"
        args.append(user["id"])
    else:
        sql += " AND status IN ('approved','completed')"
    if q.get("category"):
        sql += " AND category=?"
        args.append(q["category"])
    if q.get("q"):
        sql += " AND (title LIKE ? OR summary LIKE ?)"
        args += [f"%{q['q']}%"] * 2
    sort = {"new": "created_at DESC", "funded": "id DESC"}.get(q.get("sort"), "created_at DESC")
    rows = db.execute(sql + f" ORDER BY {sort} LIMIT 200", args).fetchall()
    cards = [project_card(db, r) for r in rows]
    if q.get("sort") == "funded":
        cards.sort(key=lambda c: c["raised"], reverse=True)
    return 200, {"projects": cards}


def get_project(ctx, pid):
    db, user = ctx["db"], ctx["user"]
    p = load_project(db, int(pid))
    is_owner = bool(user and user["id"] == p["student_id"])
    is_teacher = bool(user and user["role"] == "teacher")
    supporter = is_supporter(db, user, p["id"])
    if p["status"] in ("pending", "rejected") and not (is_owner or is_teacher):
        raise ApiError(404, "Project not found")
    d = project_card(db, p)
    d["description"] = p["description"]
    d["teacher_feedback"] = p["teacher_feedback"]
    d["milestones"] = [dict(m) for m in db.execute("SELECT * FROM milestones WHERE project_id=? ORDER BY id", (p["id"],))]
    can_monitor = is_owner or is_teacher or supporter
    d["can_monitor"] = can_monitor
    d["is_owner"], d["is_supporter"] = is_owner, supporter
    d["has_bought"] = bool(user and db.execute(
        "SELECT 1 FROM contributions WHERE project_id=? AND user_id=? AND kind='buy'", (p["id"], user["id"])).fetchone())
    if can_monitor:
        d["updates"] = [dict(r) for r in db.execute(
            "SELECT u.*, us.name author, us.role author_role FROM updates u JOIN users us ON us.id=u.user_id "
            "WHERE project_id=? ORDER BY u.id DESC LIMIT 200", (p["id"],))]
    else:
        d["updates"] = []
    # deliverable only for buyers (or the owner/teacher) once the work is completed
    d["deliverable_url"] = p["deliverable_url"] if (is_owner or is_teacher or (d["has_bought"] and p["status"] == "completed")) else ""
    return 200, d


def create_project(ctx):
    user = need_role(ctx, "student")
    b, db = ctx["body"], ctx["db"]
    title = clean(b.get("title"), "Title", 120)
    summary = clean(b.get("summary"), "Summary", 300)
    desc = clean(b.get("description"), "Description", 5000, required=False)
    cat = b.get("category") if b.get("category") in CATEGORIES else "Other"
    price = money(b.get("price"), "Price")
    goal = money(b.get("goal"), "Funding goal")
    ms = b.get("milestones") or []
    if not isinstance(ms, list) or not 1 <= len(ms) <= 20:
        raise ApiError(400, "Add between 1 and 20 milestones so progress can be tracked")
    idea_id = b.get("idea_id")
    if idea_id and not db.execute("SELECT 1 FROM ideas WHERE id=?", (idea_id,)).fetchone():
        idea_id = None
    cur = db.execute(
        "INSERT INTO projects(student_id,title,summary,description,category,price,goal,idea_id,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)", (user["id"], title, summary, desc, cat, price, goal, idea_id, now()))
    pid = cur.lastrowid
    for m in ms:
        w = int(m.get("weight") or 1)
        db.execute("INSERT INTO milestones(project_id,title,weight) VALUES(?,?,?)",
                   (pid, clean(m.get("title"), "Milestone title", 120), max(1, min(w, 100))))
    if idea_id:
        db.execute("UPDATE ideas SET claimed_by=?, project_id=? WHERE id=?", (user["id"], pid, idea_id))
    return 201, {"id": pid}


def edit_project(ctx, pid):
    user, p = own_project(ctx, int(pid))
    b, db = ctx["body"], ctx["db"]
    fields = {
        "title": clean(b.get("title", p["title"]), "Title", 120),
        "summary": clean(b.get("summary", p["summary"]), "Summary", 300),
        "description": clean(b.get("description", p["description"]), "Description", 5000, required=False),
        "deliverable_url": clean(b.get("deliverable_url", p["deliverable_url"]), "Deliverable link", 500, required=False),
    }
    if fields["deliverable_url"] and not re.match(r"https?://", fields["deliverable_url"]):
        raise ApiError(400, "Deliverable link must start with http:// or https://")
    if "price" in b:
        fields["price"] = money(b["price"], "Price")
    if "goal" in b:
        fields["goal"] = money(b["goal"], "Funding goal")
    status = "pending" if p["status"] == "rejected" else p["status"]  # resubmit after rejection
    sets = ", ".join(f"{k}=?" for k in fields)
    db.execute(f"UPDATE projects SET {sets}, status=? WHERE id=?", [*fields.values(), status, p["id"]])
    return 200, {"ok": True}


def review_project(ctx, pid):
    teacher = need_role(ctx, "teacher")
    b, db = ctx["body"], ctx["db"]
    p = load_project(db, int(pid))
    decision = b.get("decision")
    if decision not in ("approve", "reject"):
        raise ApiError(400, "decision must be approve or reject")
    fb = clean(b.get("feedback"), "Feedback", 1000, required=decision == "reject")
    status = "approved" if decision == "approve" else "rejected"
    db.execute("UPDATE projects SET status=?, teacher_feedback=?, reviewed_by=? WHERE id=?", (status, fb, teacher["id"], p["id"]))
    system_note(db, p["id"], teacher["id"], f"Teacher {teacher['name']} {status} this project." + (f" Feedback: {fb}" if fb else ""))
    return 200, {"status": status}


# --------------------------------------------------------------------------- milestones & updates
def add_milestone(ctx, pid):
    user, p = own_project(ctx, int(pid))
    if p["status"] == "completed":
        raise ApiError(400, "Project is already completed")
    b, db = ctx["body"], ctx["db"]
    w = max(1, min(int(b.get("weight") or 1), 100))
    db.execute("INSERT INTO milestones(project_id,title,weight) VALUES(?,?,?)", (p["id"], clean(b.get("title"), "Title", 120), w))
    refresh_completion(db, p["id"])
    return 201, {"ok": True}


def load_milestone(db, mid):
    m = db.execute("SELECT * FROM milestones WHERE id=?", (mid,)).fetchone()
    if not m:
        raise ApiError(404, "Milestone not found")
    return m


def milestone_done(ctx, mid):
    db = ctx["db"]
    m = load_milestone(db, int(mid))
    user, p = own_project(ctx, m["project_id"])
    if p["status"] not in ("approved", "completed"):
        raise ApiError(400, "Project must be approved by a teacher before work is tracked")
    if m["status"] != "todo":
        raise ApiError(400, "Milestone is already marked done")
    db.execute("UPDATE milestones SET status='done', done_at=? WHERE id=?", (now(), m["id"]))
    note = clean(ctx["body"].get("note"), "Note", 500, required=False)
    system_note(db, p["id"], user["id"], f"Student marked milestone '{m['title']}' as done - waiting for teacher verification." + (f" Note: {note}" if note else ""))
    return 200, {"ok": True}


def milestone_verify(ctx, mid):
    teacher = need_role(ctx, "teacher")
    db = ctx["db"]
    m = load_milestone(db, int(mid))
    action = ctx["body"].get("action", "verify")
    note = clean(ctx["body"].get("note"), "Note", 500, required=action == "reopen")
    if action == "verify":
        if m["status"] != "done":
            raise ApiError(400, "Only milestones marked done by the student can be verified")
        db.execute("UPDATE milestones SET status='verified', verified_at=?, teacher_note=? WHERE id=?", (now(), note, m["id"]))
        system_note(db, m["project_id"], teacher["id"], f"Teacher verified milestone '{m['title']}'." + (f" Note: {note}" if note else ""))
    elif action == "reopen":
        db.execute("UPDATE milestones SET status='todo', teacher_note=?, verified_at=NULL, done_at=NULL WHERE id=?", (note, m["id"]))
        system_note(db, m["project_id"], teacher["id"], f"Teacher sent milestone '{m['title']}' back for rework. Reason: {note}")
    else:
        raise ApiError(400, "action must be verify or reopen")
    refresh_completion(db, m["project_id"])
    return 200, {"ok": True}


def post_update(ctx, pid):
    """Students post progress updates; teachers, buyers and backers post comments."""
    user = need_role(ctx, "student", "teacher", "customer")
    db = ctx["db"]
    p = load_project(db, int(pid))
    owner = user["id"] == p["student_id"]
    if not (owner or user["role"] == "teacher" or is_supporter(db, user, p["id"])):
        raise ApiError(403, "Only the student, teachers and supporters of this project can post here")
    body = clean(ctx["body"].get("body"), "Message", 1500)
    db.execute("INSERT INTO updates(project_id,user_id,kind,body,created_at) VALUES(?,?,?,?,?)",
               (p["id"], user["id"], "update" if owner else "comment", body, now()))
    return 201, {"ok": True}


# --------------------------------------------------------------------------- money (simulated)
def contribute(ctx, pid, kind):
    user = need_role(ctx, "customer")
    db = ctx["db"]
    p = load_project(db, int(pid))
    if p["status"] not in ("approved", "completed"):
        raise ApiError(400, "This project is not open for funding yet")
    if kind == "buy":
        if is_bought(db, user, p["id"]):
            raise ApiError(409, "You already bought this project")
        amount = p["price"]
    else:
        if p["status"] == "completed":
            raise ApiError(400, "This project is completed - you can buy it instead")
        amount = money(ctx["body"].get("amount"), "Amount")
    # ---- real payment gateway call would go here ----
    db.execute("INSERT INTO contributions(project_id,user_id,amount,kind,created_at) VALUES(?,?,?,?,?)",
               (p["id"], user["id"], amount, kind, now()))
    verb = "bought" if kind == "buy" else "funded"
    system_note(db, p["id"], user["id"], f"{user['name']} {verb} this project (Rs {amount:,.0f}).")
    return 201, {"ok": True, "amount": amount}


def is_bought(db, user, pid):
    return db.execute("SELECT 1 FROM contributions WHERE project_id=? AND user_id=? AND kind='buy'", (pid, user["id"])).fetchone() is not None


# --------------------------------------------------------------------------- ideas
def idea_row(db, r, user):
    d = dict(r)
    d["author"] = db.execute("SELECT name, role FROM users WHERE id=?", (r["user_id"],)).fetchone()["name"]
    d["votes"] = db.execute("SELECT COUNT(*) c FROM idea_votes WHERE idea_id=?", (r["id"],)).fetchone()["c"]
    d["voted"] = bool(user and db.execute("SELECT 1 FROM idea_votes WHERE idea_id=? AND user_id=?", (r["id"], user["id"])).fetchone())
    if r["claimed_by"]:
        d["claimed_by_name"] = db.execute("SELECT name FROM users WHERE id=?", (r["claimed_by"],)).fetchone()["name"]
    return d


def list_ideas(ctx):
    db = ctx["db"]
    rows = [idea_row(db, r, ctx["user"]) for r in db.execute("SELECT * FROM ideas ORDER BY id DESC LIMIT 200")]
    rows.sort(key=lambda i: (i["votes"], i["id"]), reverse=True)
    return 200, {"ideas": rows}


def create_idea(ctx):
    user = need_role(ctx, "student", "teacher", "customer")
    b, db = ctx["body"], ctx["db"]
    cat = b.get("category") if b.get("category") in CATEGORIES else "Other"
    cur = db.execute("INSERT INTO ideas(user_id,title,description,category,created_at) VALUES(?,?,?,?,?)",
                     (user["id"], clean(b.get("title"), "Title", 120), clean(b.get("description"), "Description", 2000), cat, now()))
    return 201, {"id": cur.lastrowid}


def vote_idea(ctx, iid):
    user = need_role(ctx, "student", "teacher", "customer")
    db = ctx["db"]
    if not db.execute("SELECT 1 FROM ideas WHERE id=?", (int(iid),)).fetchone():
        raise ApiError(404, "Idea not found")
    if db.execute("SELECT 1 FROM idea_votes WHERE idea_id=? AND user_id=?", (int(iid), user["id"])).fetchone():
        db.execute("DELETE FROM idea_votes WHERE idea_id=? AND user_id=?", (int(iid), user["id"]))
    else:
        db.execute("INSERT INTO idea_votes VALUES(?,?)", (int(iid), user["id"]))
    return 200, {"ok": True}


# --------------------------------------------------------------------------- dashboard
def dashboard(ctx):
    user = need_role(ctx, "student", "teacher", "customer")
    db = ctx["db"]
    out = {"role": user["role"]}
    if user["role"] == "student":
        rows = db.execute("SELECT * FROM projects WHERE student_id=?", (user["id"],)).fetchall()
        ids = [r["id"] for r in rows]
        raised = db.execute(f"SELECT COALESCE(SUM(amount),0) s FROM contributions WHERE project_id IN ({','.join('?'*len(ids)) or 'NULL'})", ids).fetchone()["s"]
        out["stats"] = {"Projects": len(rows), "Pending review": sum(r["status"] == "pending" for r in rows),
                        "Completed": sum(r["status"] == "completed" for r in rows), "Total raised (Rs)": raised}
    elif user["role"] == "teacher":
        out["stats"] = {
            "Awaiting approval": db.execute("SELECT COUNT(*) c FROM projects WHERE status='pending'").fetchone()["c"],
            "Milestones to verify": db.execute("SELECT COUNT(*) c FROM milestones m JOIN projects p ON p.id=m.project_id WHERE m.status='done'").fetchone()["c"],
            "Projects reviewed": db.execute("SELECT COUNT(*) c FROM projects WHERE reviewed_by=?", (user["id"],)).fetchone()["c"]}
        out["to_verify"] = [dict(r) for r in db.execute(
            "SELECT m.id, m.title, m.project_id, p.title project_title, m.done_at FROM milestones m "
            "JOIN projects p ON p.id=m.project_id WHERE m.status='done' ORDER BY m.done_at")]
    else:
        r = db.execute("SELECT COUNT(DISTINCT project_id) n, COALESCE(SUM(amount),0) s FROM contributions WHERE user_id=?", (user["id"],)).fetchone()
        out["stats"] = {"Projects supported": r["n"], "Total spent (Rs)": r["s"],
                        "Purchases": db.execute("SELECT COUNT(*) c FROM contributions WHERE user_id=? AND kind='buy'", (user["id"],)).fetchone()["c"]}
    return 200, out


# --------------------------------------------------------------------------- routing
ROUTES = [
    ("POST", r"/api/register", register), ("POST", r"/api/login", login),
    ("POST", r"/api/logout", logout), ("GET", r"/api/me", me),
    ("GET", r"/api/dashboard", dashboard),
    ("GET", r"/api/projects", list_projects), ("POST", r"/api/projects", create_project),
    ("GET", r"/api/projects/(\d+)", get_project), ("PUT", r"/api/projects/(\d+)", edit_project),
    ("POST", r"/api/projects/(\d+)/review", review_project),
    ("POST", r"/api/projects/(\d+)/milestones", add_milestone),
    ("POST", r"/api/projects/(\d+)/updates", post_update),
    ("POST", r"/api/projects/(\d+)/fund", lambda c, i: contribute(c, i, "fund")),
    ("POST", r"/api/projects/(\d+)/buy", lambda c, i: contribute(c, i, "buy")),
    ("POST", r"/api/milestones/(\d+)/done", milestone_done),
    ("POST", r"/api/milestones/(\d+)/verify", milestone_verify),
    ("GET", r"/api/ideas", list_ideas), ("POST", r"/api/ideas", create_idea),
    ("POST", r"/api/ideas/(\d+)/vote", vote_idea),
]
ROUTES = [(m, re.compile(p + r"$"), f) for m, p, f in ROUTES]


class Handler(BaseHTTPRequestHandler):
    server_version = "CollegeCrowdfund/1.0"

    def log_message(self, fmt, *args):
        if os.environ.get("QUIET") != "1":
            sys.stderr.write("%s %s\n" % (self.command, self.path))

    def send_json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def handle_api(self, method, url):
        path = url.path.rstrip("/") or "/"
        db = connect()
        try:
            ctx = {"db": db, "query": {k: v[0] for k, v in parse_qs(url.query).items()}, "body": {}, "user": None, "token": None}
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                ctx["token"] = auth[7:]
                row = db.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.created_at>?",
                                 (ctx["token"], now() - SESSION_TTL)).fetchone()
                ctx["user"] = row
            if method in ("POST", "PUT"):
                length = int(self.headers.get("Content-Length") or 0)
                if length > MAX_BODY:
                    raise ApiError(413, "Request too large")
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    ctx["body"] = json.loads(raw or b"{}")
                    if not isinstance(ctx["body"], dict):
                        raise ValueError
                except ValueError:
                    raise ApiError(400, "Body must be a JSON object")
            for m, rx, fn in ROUTES:
                match = rx.match(path)
                if match and m == method:
                    code, payload = fn(ctx, *match.groups())
                    db.commit()
                    return self.send_json(code, payload)
            if any(rx.match(path) for _, rx, _ in ROUTES):
                raise ApiError(405, "Method not allowed")
            raise ApiError(404, "Unknown API route")
        except ApiError as e:
            db.rollback()
            self.send_json(e.code, {"error": e.msg})
        except Exception as e:  # never leak internals
            db.rollback()
            sys.stderr.write(f"ERROR {type(e).__name__}: {e}\n")
            self.send_json(500, {"error": "Server error"})
        finally:
            db.close()

    def serve_static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(os.path.realpath(STATIC_DIR) + os.sep) or not os.path.isfile(full):
            full = os.path.join(STATIC_DIR, "index.html")  # SPA fallback
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or "javascript" in ctype else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def route(self, method):
        url = urlparse(self.path)
        if url.path.startswith("/api/"):
            self.handle_api(method, url)
        elif method == "GET":
            self.serve_static(url.path)
        else:
            self.send_json(405, {"error": "Method not allowed"})

    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_PUT(self): self.route("PUT")


# --------------------------------------------------------------------------- demo data
def seed():
    db = connect()
    if db.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
        db.close()
        return

    def user(name, email, role, college):
        salt = secrets.token_hex(16)
        return db.execute("INSERT INTO users(name,email,pw_hash,salt,role,college,created_at) VALUES(?,?,?,?,?,?,?)",
                          (name, email, hash_pw("demo123", salt), salt, role, college, now())).lastrowid

    teacher = user("Prof. Meera Sharma", "teacher@demo.com", "teacher", "Lucknow Institute of Technology")
    s1 = user("Aarav Verma", "student@demo.com", "student", "Lucknow Institute of Technology")
    s2 = user("Isha Gupta", "isha@demo.com", "student", "Lucknow Institute of Technology")
    cust = user("Rohan Mehta", "customer@demo.com", "customer", "")

    def project(sid, title, summary, desc, cat, price, goal, status, miles):
        pid = db.execute("INSERT INTO projects(student_id,title,summary,description,category,price,goal,status,reviewed_by,teacher_feedback,created_at) "
                         "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid, title, summary, desc, cat, price, goal, status,
                                                         teacher if status != "pending" else None,
                                                         "Good scope. Approved." if status != "pending" else "", now())).lastrowid
        for t, w, st in miles:
            db.execute("INSERT INTO milestones(project_id,title,weight,status,done_at,verified_at) VALUES(?,?,?,?,?,?)",
                       (pid, t, w, st, now() if st != "todo" else None, now() if st == "verified" else None))
        return pid

    p1 = project(s1, "Smart Attendance System", "Face-recognition attendance for classrooms with a teacher dashboard.",
                 "Uses OpenCV + a lightweight web dashboard. Delivers source code, setup guide and a viva-ready report.",
                 "AI/ML", 4500, 15000, "approved",
                 [("Requirement analysis & design", 1, "verified"), ("Face detection module", 3, "verified"),
                  ("Dashboard UI", 2, "done"), ("Testing & documentation", 2, "todo")])
    project(s2, "Campus Bus Tracker", "Live bus location app for college transport.",
            "Android app + GPS device firmware + admin panel.", "IoT", 6000, 20000, "approved",
            [("Hardware prototype", 3, "verified"), ("Mobile app", 3, "todo"), ("Admin panel", 2, "todo")])
    project(s1, "Library Book Finder", "Search the shelf location of any library book.", "Web app with barcode scanning.",
            "Web", 2500, 8000, "pending", [("Database design", 1, "todo"), ("Search UI", 2, "todo")])
    db.execute("INSERT INTO contributions(project_id,user_id,amount,kind,created_at) VALUES(?,?,?,?,?)", (p1, cust, 4500, "buy", now()))
    system_note(db, p1, cust, "Rohan Mehta bought this project (Rs 4,500).")
    db.execute("INSERT INTO updates(project_id,user_id,kind,body,created_at) VALUES(?,?,?,?,?)",
               (p1, s1, "update", "Face detection works at 92% accuracy on our test set. Starting the dashboard next week.", now()))
    for uid, t, d, c in [(cust, "Hostel mess feedback app", "Students rate meals daily; the warden sees trends.", "Web"),
                         (teacher, "Plagiarism checker for lab reports", "Compare submitted lab reports against previous batches.", "AI/ML"),
                         (s2, "Smart energy meter for hostels", "Per-room electricity usage with alerts.", "IoT")]:
        db.execute("INSERT INTO ideas(user_id,title,description,category,created_at) VALUES(?,?,?,?,?)", (uid, t, d, c, now()))
    db.commit()
    db.close()
    print("Seeded demo accounts (password: demo123): teacher@demo.com, student@demo.com, customer@demo.com")


def main():
    init_db()
    if os.environ.get("NO_SEED") != "1":
        seed()
    host = os.environ.get("HOST", "127.0.0.1")
    srv, port = None, PORT
    for p in range(PORT, PORT + 11):  # if the port is busy, try the next ones
        try:
            srv, port = ThreadingHTTPServer((host, p), Handler), p
            break
        except OSError:
            continue
    if srv is None:
        sys.exit(f"Could not start: ports {PORT}-{PORT + 10} are all busy. Close other programs or set PORT=9000.")
    url = f"http://localhost:{port}"
    print("=" * 56)
    print(f"  College Crowdfunding Platform is RUNNING")
    print(f"  Open this in your browser:  {url}")
    print(f"  Keep this window open. Press Ctrl+C to stop.")
    print("=" * 56)
    if os.environ.get("NO_BROWSER") != "1":
        try:
            import threading, webbrowser
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nBye!")


if __name__ == "__main__":
    main()
