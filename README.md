# College Crowdfunding Platform

A crowdfunding and project-marketplace platform for college students, with **student, teacher and customer** logins.
Customers who buy or back a project can **monitor exactly what percentage of the work is done**, and that percentage is **verified by a teacher**, not just claimed by the student.

## Run it (no installs needed)

Requires only **Python 3.8+** (https://www.python.org/downloads/ - tick "Add Python to PATH" on Windows).

1. **Unzip the folder first** (do not run it from inside the zip).
2. **Windows:** double-click `start.bat`. **Mac/Linux:** run `sh start.sh`.
   Or from a terminal inside the folder: `python server.py` (Windows) / `python3 server.py` (Mac/Linux).
3. Your browser opens automatically. If not, open the address printed in the black window (usually **http://localhost:8000**).
4. **Keep that window open** while using the site.

> Do NOT double-click `static/index.html` - the page needs the server running to work.

Demo accounts (password `demo123`):

| Role     | Email               |
|----------|---------------------|
| Student  | student@demo.com    |
| Teacher  | teacher@demo.com    |
| Customer | customer@demo.com   |

Start fresh with demo data by deleting `crowdfunding.db`. Skip demo data with `NO_SEED=1 python3 server.py`.
Change the port with `PORT=5000 python3 server.py`.

## How it works

1. **Student** submits a project with a price, funding goal and **milestones** (each with a weight = effort).
2. **Teacher** reviews it: approve (it goes public) or reject with feedback (student fixes and resubmits).
3. **Customer** can **Buy** the project at its price or **Fund** it with any amount.
   Doing either unlocks the live monitoring view for that project.
4. As the student works they mark milestones **done** and post updates. The teacher **verifies** each one
   (or sends it back for rework). The **progress %** = verified weight / total weight.
   The bar shows two layers: green = teacher verified, yellow = claimed by student but not yet verified.
5. When every milestone is verified the project becomes **Completed**; buyers then get the **deliverable link**.
6. **Ideas board**: anyone can suggest a project idea; everyone can upvote; students click **Build this**
   to turn an idea into a project.

## Folder structure

```
college-crowdfunding-platform/
├── server.py            # backend: HTTP server + REST API + SQLite (stdlib only)
├── crowdfunding.db      # created automatically on first run
├── static/
│   ├── index.html       # app shell
│   ├── style.css        # styling
│   └── app.js           # frontend single-page app
└── tests/smoke_test.py  # 40-check end-to-end test
```

## API summary

| Method | Path | Who |
|---|---|---|
| POST | /api/register, /api/login, /api/logout | all |
| GET | /api/me, /api/dashboard | logged in |
| GET | /api/projects?scope=public\|mine\|pending\|supported&q=&category=&sort= | varies |
| POST | /api/projects | student |
| GET / PUT | /api/projects/:id | all / owner |
| POST | /api/projects/:id/review | teacher |
| POST | /api/projects/:id/milestones | owner student |
| POST | /api/milestones/:id/done | owner student |
| POST | /api/milestones/:id/verify | teacher |
| POST | /api/projects/:id/updates | owner, teachers, supporters |
| POST | /api/projects/:id/fund, /buy | customer |
| GET / POST | /api/ideas, POST /api/ideas/:id/vote | all / logged in |

## Test

```bash
python3 tests/smoke_test.py
```

## Important notes before real use

- **Payments are simulated.** No money moves. Integrate Razorpay/Stripe inside `contribute()` in `server.py`.
- Anyone can sign up as a teacher. In production, restrict teacher accounts (admin approval or college email domain).
- Run behind HTTPS (nginx/Caddy) when deployed. Passwords are hashed (PBKDF2) and all user text is escaped.
