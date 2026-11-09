#!/usr/bin/env python3
"""End-to-end test: starts the server on a temp DB and walks the whole workflow.
Run:  python3 tests/smoke_test.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        sys.exit(1)
    passed += 1


def main():
    tmp = tempfile.mkdtemp()
    env = {**os.environ, "PORT": str(PORT), "DB_PATH": os.path.join(tmp, "t.db"), "NO_SEED": "1", "QUIET": "1"}
    srv = subprocess.Popen([sys.executable, os.path.join(ROOT, "server.py")], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(BASE + "/"); break
            except Exception:
                time.sleep(0.1)

        def reg(role, n):
            s, r = call("POST", "/api/register", {"name": n, "email": f"{n}@x.com", "password": "secret1", "role": role})
            assert s == 201, r
            return r["token"]

        st, te, cu = reg("student", "stu"), reg("teacher", "tea"), reg("customer", "cus")
        check("register 3 roles", True)
        check("duplicate email rejected", call("POST", "/api/register", {"name": "a", "email": "stu@x.com", "password": "secret1", "role": "student"})[0] == 409)
        check("bad login rejected", call("POST", "/api/login", {"email": "stu@x.com", "password": "nope"})[0] == 401)
        check("login works", call("POST", "/api/login", {"email": "stu@x.com", "password": "secret1"})[0] == 200)

        s, r = call("POST", "/api/projects", {"title": "Demo", "summary": "s", "price": 1000, "goal": 5000, "category": "Web",
                                              "milestones": [{"title": "A", "weight": 1}, {"title": "B", "weight": 3}]}, st)
        check("student creates project", s == 201)
        pid = r["id"]
        check("customer cannot create project", call("POST", "/api/projects", {"title": "x"}, cu)[0] == 403)
        check("pending project hidden from public", call("GET", f"/api/projects/{pid}")[0] == 404)
        check("cannot fund unapproved", call("POST", f"/api/projects/{pid}/fund", {"amount": 10}, cu)[0] == 400)
        check("student cannot self-approve", call("POST", f"/api/projects/{pid}/review", {"decision": "approve"}, st)[0] == 403)
        check("teacher approves", call("POST", f"/api/projects/{pid}/review", {"decision": "approve", "feedback": "ok"}, te)[0] == 200)
        check("project now public", call("GET", f"/api/projects/{pid}")[0] == 200)

        check("customer buys", call("POST", f"/api/projects/{pid}/buy", None, cu)[0] == 201)
        check("double purchase blocked", call("POST", f"/api/projects/{pid}/buy", None, cu)[0] == 409)
        check("customer funds extra", call("POST", f"/api/projects/{pid}/fund", {"amount": 500}, cu)[0] == 201)
        check("negative amount rejected", call("POST", f"/api/projects/{pid}/fund", {"amount": -5}, cu)[0] == 400)

        d = call("GET", f"/api/projects/{pid}", token=cu)[1]
        check("buyer can monitor (activity visible)", d["can_monitor"] and len(d["updates"]) >= 2)
        check("anonymous cannot see activity", call("GET", f"/api/projects/{pid}")[1]["updates"] == [])
        check("progress starts at 0%", d["progress"]["verified"] == 0 and d["raised"] == 1500)

        mA, mB = d["milestones"][0]["id"], d["milestones"][1]["id"]
        check("student marks A done", call("POST", f"/api/milestones/{mA}/done", {}, st)[0] == 200)
        d = call("GET", f"/api/projects/{pid}", token=cu)[1]
        check("claimed 25%, verified still 0%", d["progress"]["claimed"] == 25 and d["progress"]["verified"] == 0)
        check("student cannot verify own work", call("POST", f"/api/milestones/{mA}/verify", {"action": "verify"}, st)[0] == 403)
        check("teacher verifies A -> 25%", call("POST", f"/api/milestones/{mA}/verify", {"action": "verify"}, te)[0] == 200)
        check("buyer sees 25% verified", call("GET", f"/api/projects/{pid}", token=cu)[1]["progress"]["verified"] == 25)

        call("POST", f"/api/milestones/{mB}/done", {}, st)
        check("teacher can send back for rework", call("POST", f"/api/milestones/{mB}/verify", {"action": "reopen", "note": "add tests"}, te)[0] == 200)
        call("POST", f"/api/milestones/{mB}/done", {}, st)
        call("POST", f"/api/milestones/{mB}/verify", {"action": "verify"}, te)
        d = call("GET", f"/api/projects/{pid}", token=cu)[1]
        check("100% verified -> project completed", d["progress"]["verified"] == 100 and d["status"] == "completed")

        check("deliverable hidden before upload", d["deliverable_url"] == "")
        check("student sets deliverable link", call("PUT", f"/api/projects/{pid}", {"deliverable_url": "https://example.com/code.zip"}, st)[0] == 200)
        check("buyer gets deliverable", call("GET", f"/api/projects/{pid}", token=cu)[1]["deliverable_url"] == "https://example.com/code.zip")
        check("public cannot get deliverable", call("GET", f"/api/projects/{pid}")[1]["deliverable_url"] == "")

        check("buyer can comment", call("POST", f"/api/projects/{pid}/updates", {"body": "Great work!"}, cu)[0] == 201)

        s, r = call("POST", "/api/ideas", {"title": "Idea", "description": "desc", "category": "IoT"}, cu)
        check("customer suggests idea", s == 201)
        check("upvote idea", call("POST", f"/api/ideas/{r['id']}/vote", None, st)[0] == 200)
        check("idea has 1 vote", call("GET", "/api/ideas")[1]["ideas"][0]["votes"] == 1)
        s, r2 = call("POST", "/api/projects", {"title": "From idea", "summary": "s", "price": 10, "goal": 10, "idea_id": r["id"],
                                               "milestones": [{"title": "x"}]}, st)
        check("student builds from idea", s == 201 and call("GET", "/api/ideas")[1]["ideas"][0]["claimed_by"] is not None)

        for role, tok in (("student", st), ("teacher", te), ("customer", cu)):
            check(f"{role} dashboard", call("GET", "/api/dashboard", token=tok)[0] == 200)
        check("XSS-style text stored as plain text", call("POST", f"/api/projects/{pid}/updates", {"body": "<script>alert(1)</script>"}, cu)[0] == 201)
        check("static frontend served", urllib.request.urlopen(BASE + "/app.js").status == 200)
        check("path traversal blocked", b"root:" not in urllib.request.urlopen(BASE + "/..%2f..%2f..%2fetc/passwd").read())
        print(f"\nAll {passed} checks passed.")
    finally:
        srv.terminate()


if __name__ == "__main__":
    main()
