/* College Crowdfunding Platform - single-page frontend (no build step, no dependencies).
   All user text is inserted with textContent / text nodes, so it cannot inject HTML. */
const CATEGORIES = ["Web", "Mobile", "AI/ML", "IoT", "Data", "Hardware", "Research", "Other"];
const S = { user: null, token: localStorage.getItem("cf_token") };

// ---------- tiny helpers ----------
function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "class") e.className = v;
    else if (k === "value") e.value = v;
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  kids.flat(Infinity).forEach(c => { if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(c)); });
  return e;
}
const inr = n => "Rs " + Number(n || 0).toLocaleString("en-IN", { maximumFractionDigits: 0 });
const when = t => new Date(t * 1000).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
const pct = (a, b) => (b > 0 ? Math.min(100, Math.round((100 * a) / b)) : 0);

function toast(msg, err) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast.t); toast.t = setTimeout(() => (t.className = "toast"), 3200);
}

async function api(path, method = "GET", body) {
  const res = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: "Bearer " + S.token } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Something went wrong");
  return data;
}
// run an action, show errors as toast, then refresh the page
const act = (fn, ok) => async () => { try { await fn(); if (ok) toast(ok); route(); } catch (e) { toast(e.message, true); } };

function badge(status) { return h("span", { class: "badge b-" + status }, status); }

function progressBar(p) {
  return h("div", null,
    h("div", { class: "bar", title: `${p.verified}% verified, ${p.claimed}% claimed by student` },
      h("i", { class: "claimed", style: `width:${p.claimed}%` }), h("i", { class: "verified", style: `width:${p.verified}%` })));
}
function fundBar(raised, goal) {
  return h("div", { class: "bar fund" }, h("i", { style: `width:${pct(raised, goal)}%` }));
}

function projectCard(p) {
  return h("a", { href: "#/project/" + p.id, class: "card", style: "color:inherit;display:block" },
    h("div", { class: "row between" }, h("span", { class: "pill" }, p.category), badge(p.status)),
    h("h3", { style: "margin-top:10px" }, p.title),
    h("p", { class: "muted small" }, p.summary),
    h("p", { class: "small" }, "by ", h("b", null, p.student_name)),
    h("div", { class: "small row between" }, h("span", null, "Work done (teacher verified)"), h("b", null, p.progress.verified + "%")),
    progressBar(p.progress),
    h("div", { class: "small row between", style: "margin-top:12px" }, h("span", null, `${inr(p.raised)} of ${inr(p.goal)}`), h("b", null, pct(p.raised, p.goal) + "%")),
    fundBar(p.raised, p.goal),
    h("div", { class: "row between", style: "margin-top:12px" }, h("span", { class: "muted small" }, p.backers + " supporter(s)"), h("b", null, "Buy: " + inr(p.price))));
}

// ---------- navigation ----------
function renderNav() {
  const nav = document.getElementById("nav"); nav.replaceChildren();
  const here = location.hash || "#/";
  const link = (href, text) => h("a", { href, class: here === href ? "active" : "" }, text);
  nav.append(link("#/", "Browse"), link("#/ideas", "Ideas"));
  if (!S.user) { nav.append(link("#/login", "Log in"), h("a", { href: "#/register", class: "btn sm" }, "Sign up")); return; }
  nav.append(link("#/dashboard", "Dashboard"));
  if (S.user.role === "student") nav.append(link("#/new", "+ New project"));
  nav.append(h("span", { class: "pill" }, S.user.role), h("span", { class: "small muted" }, S.user.name),
    h("button", { onclick: logout }, "Log out"));
}
async function logout() {
  try { await api("/logout", "POST"); } catch (_) {}
  S.token = null; S.user = null; localStorage.removeItem("cf_token");
  if (location.hash === "#/" || location.hash === "") route(); else location.hash = "#/";
}
async function loadUser() {
  if (!S.token) return;
  try { S.user = (await api("/me")).user; } catch (_) { S.token = null; localStorage.removeItem("cf_token"); }
}

// ---------- pages ----------
async function pageHome() {
  const wrap = h("div");
  const q = h("input", { placeholder: "Search projects..." });
  const cat = h("select", null, h("option", { value: "" }, "All categories"), CATEGORIES.map(c => h("option", { value: c }, c)));
  const sort = h("select", null, h("option", { value: "new" }, "Newest"), h("option", { value: "funded" }, "Most funded"));
  const list = h("div", { class: "grid" });
  async function load() {
    const qs = new URLSearchParams({ q: q.value, category: cat.value, sort: sort.value });
    const { projects } = await api("/projects?" + qs);
    list.replaceChildren(...(projects.length ? projects.map(projectCard) : [h("div", { class: "empty" }, "No projects found yet.")]));
  }
  [q, cat, sort].forEach(el => el.addEventListener(el === q ? "input" : "change", load));
  wrap.append(
    h("div", { class: "hero" }, h("h1", null, "Fund student projects. Watch them get built."),
      h("p", null, "Buy or back real projects by college students, and track the exact percentage of work completed - verified by a teacher."),
      h("div", { class: "row" }, h("div", { style: "flex:2;min-width:200px" }, q), h("div", { style: "flex:1;min-width:140px" }, cat), h("div", { style: "flex:1;min-width:140px" }, sort))),
    list);
  await load(); return wrap;
}

function authForm(register) {
  const f = {
    name: h("input", { required: true }), email: h("input", { type: "email", required: true }),
    password: h("input", { type: "password", required: true, minlength: 6 }), college: h("input"),
    role: h("select", null, h("option", { value: "student" }, "Student - build & sell projects"),
      h("option", { value: "customer" }, "Customer - fund & buy projects"), h("option", { value: "teacher" }, "Teacher - guide & verify work")),
  };
  const field = (l, el) => h("div", null, h("label", null, l), el);
  const form = h("form", { class: "card form stack", onsubmit: async ev => {
    ev.preventDefault();
    try {
      const body = register ? Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value])) : { email: f.email.value, password: f.password.value };
      const r = await api(register ? "/register" : "/login", "POST", body);
      S.token = r.token; S.user = r.user; localStorage.setItem("cf_token", r.token);
      location.hash = "#/dashboard"; route();
    } catch (e) { toast(e.message, true); }
  } },
    h("h2", null, register ? "Create your account" : "Welcome back"),
    register && field("Full name", f.name), field("Email", f.email), field("Password", f.password),
    register && field("College (optional)", f.college), register && field("I am a...", f.role),
    h("button", { class: "btn", type: "submit" }, register ? "Sign up" : "Log in"),
    h("p", { class: "small muted" }, register ? ["Have an account? ", h("a", { href: "#/login" }, "Log in")] : ["New here? ", h("a", { href: "#/register" }, "Sign up")]),
    !register && h("p", { class: "small muted" }, "Demo logins (password demo123): student@demo.com, teacher@demo.com, customer@demo.com"));
  return form;
}

async function pageProject(id) {
  const p = await api("/projects/" + id);
  const u = S.user, role = u && u.role;
  const wrap = h("div", { class: "stack" });
  wrap.append(h("a", { href: "#/" }, "< Back to projects"));

  // header
  wrap.append(h("div", { class: "card" },
    h("div", { class: "row between" }, h("div", { class: "row" }, h("span", { class: "pill" }, p.category), badge(p.status)),
      h("span", { class: "muted small" }, "Posted " + when(p.created_at))),
    h("h1", null, p.title), h("p", { class: "muted" }, p.summary),
    h("p", { class: "small" }, "By ", h("b", null, p.student_name), p.college ? " - " + p.college : ""),
    p.description && h("p", { style: "white-space:pre-wrap" }, p.description),
    p.teacher_feedback && h("div", { class: "notice" }, h("b", null, "Teacher feedback: "), p.teacher_feedback)));

  const left = h("div", { class: "stack" }), right = h("div", { class: "stack" });

  // progress (the core "surveillance" view)
  left.append(h("div", { class: "card" },
    h("h2", null, "Work progress"),
    h("div", { class: "row between" }, h("div", { class: "big-progress" }, p.progress.verified + "%"),
      h("div", { class: "muted small" }, `${p.progress.claimed}% claimed by student`)),
    h("div", { style: "margin:10px 0" }, progressBar(p.progress)),
    h("div", { class: "legend" }, h("span", null, h("i", { style: "background:var(--ok)" }), "Teacher verified"), h("span", null, h("i", { style: "background:#fcd34d" }), "Marked done, awaiting verification")),
    h("div", { style: "margin-top:14px" }, p.milestones.map(m => milestoneRow(p, m, role))),
    p.is_owner && p.status !== "completed" && addMilestoneForm(p)));

  // activity feed
  left.append(h("div", { class: "card feed" }, h("h2", null, "Activity & monitoring"),
    !p.can_monitor
      ? h("div", { class: "notice" }, "Fund or buy this project to unlock the live activity log and be able to comment on the work.")
      : [updateForm(p, role), ...(p.updates.length ? p.updates.map(updateItem) : [h("p", { class: "muted" }, "No updates yet.")])]));

  // right column: money + actions
  right.append(h("div", { class: "card" },
    h("div", { class: "row between" }, h("b", null, inr(p.raised)), h("span", { class: "muted small" }, "of " + inr(p.goal) + " goal")),
    fundBar(p.raised, p.goal), h("p", { class: "muted small" }, `${pct(p.raised, p.goal)}% funded - ${p.backers} supporter(s)`),
    h("h2", { style: "margin-top:14px" }, "Buy this project: " + inr(p.price)),
    buyBox(p, role)));

  if (p.deliverable_url) right.append(h("div", { class: "card" }, h("h2", null, "Project files"), h("a", { class: "btn ok", href: p.deliverable_url, target: "_blank", rel: "noopener noreferrer" }, "Open deliverable")));
  if (p.is_owner) right.append(ownerBox(p));
  if (role === "teacher" && p.status === "pending") right.append(reviewBox(p));
  wrap.append(h("div", { class: "two" }, left, right));
  return wrap;
}

function milestoneRow(p, m, role) {
  const actions = [];
  if (p.is_owner && m.status === "todo" && (p.status === "approved" || p.status === "completed")) {
    actions.push(h("button", { class: "btn sm", onclick: () => { const note = prompt("Optional note for your teacher:") ; if (note === null) return; act(() => api(`/milestones/${m.id}/done`, "POST", { note }), "Marked done - sent to teacher")(); } }, "Mark done"));
  }
  if (role === "teacher" && m.status === "done") {
    actions.push(h("button", { class: "btn sm ok", onclick: () => act(() => api(`/milestones/${m.id}/verify`, "POST", { action: "verify" }), "Milestone verified")() }, "Verify"),
      h("button", { class: "btn sm bad", onclick: () => { const note = prompt("Why does this need rework?"); if (note) act(() => api(`/milestones/${m.id}/verify`, "POST", { action: "reopen", note }), "Sent back for rework")(); } }, "Rework"));
  }
  return h("div", { class: "ms" },
    h("div", null, h("div", null, m.title), h("div", { class: "muted small" }, "Weight " + m.weight + (m.teacher_note ? " - Teacher: " + m.teacher_note : ""))),
    h("div", { class: "row" }, badge(m.status), actions));
}

function addMilestoneForm(p) {
  const t = h("input", { placeholder: "New milestone" }), w = h("input", { type: "number", min: 1, value: 1, style: "width:80px" });
  return h("div", { class: "row", style: "margin-top:14px" }, h("div", { style: "flex:1;min-width:160px" }, t), w,
    h("button", { class: "btn sm ghost", onclick: act(() => api(`/projects/${p.id}/milestones`, "POST", { title: t.value, weight: Number(w.value) }), "Milestone added") }, "Add"));
}

function updateForm(p, role) {
  const box = h("textarea", { placeholder: p.is_owner ? "Post a progress update for your supporters..." : "Write a comment / feedback / change request..." });
  return h("div", { style: "margin-bottom:14px" }, box,
    h("button", { class: "btn sm", style: "margin-top:8px", onclick: act(() => api(`/projects/${p.id}/updates`, "POST", { body: box.value }), "Posted") }, p.is_owner ? "Post update" : "Post comment"));
}
function updateItem(u) {
  return h("div", { class: "item " + u.kind },
    h("div", { class: "meta" }, u.kind === "system" ? "System log" : `${u.author} (${u.author_role})`, " - ", when(u.created_at)),
    h("div", { style: "white-space:pre-wrap" }, u.body));
}

function buyBox(p, role) {
  if (!S.user) return h("p", { class: "muted small" }, h("a", { href: "#/login" }, "Log in"), " as a customer to buy or fund.");
  if (role !== "customer") return h("p", { class: "muted small" }, "Only customer accounts can fund or buy projects.");
  if (p.status !== "approved" && p.status !== "completed") return h("p", { class: "muted small" }, "Not open for funding.");
  const amt = h("input", { type: "number", min: 1, placeholder: "Amount (Rs)" });
  return h("div", { class: "stack" },
    p.has_bought ? h("div", { class: "notice" }, "You own this project. You can monitor progress and get the files here once it is completed.")
      : h("button", { class: "btn", style: "width:100%", onclick: () => confirm(`Buy this project for ${inr(p.price)}? (payment is simulated)`) && act(() => api(`/projects/${p.id}/buy`, "POST"), "Purchase successful - you can now monitor this project")() }, "Buy now"),
    p.status === "approved" && h("div", null, h("label", null, "...or back it with any amount"), h("div", { class: "row" }, h("div", { style: "flex:1" }, amt),
      h("button", { class: "btn ghost", onclick: act(() => api(`/projects/${p.id}/fund`, "POST", { amount: Number(amt.value) }), "Thanks for backing this project!") }, "Fund"))));
}

function ownerBox(p) {
  const link = h("input", { placeholder: "https://drive.google.com/...", value: p.deliverable_url || "" });
  return h("div", { class: "card stack" }, h("h2", null, "Your project"),
    p.status === "rejected" && h("div", { class: "notice" }, "Rejected. Fix the issues, then resubmit."),
    p.status === "pending" && h("div", { class: "notice" }, "Waiting for a teacher to review it."),
    h("div", null, h("label", null, "Deliverable link (shown to buyers when completed)"), link),
    h("button", { class: "btn sm", onclick: act(() => api("/projects/" + p.id, "PUT", { deliverable_url: link.value }), p.status === "rejected" ? "Resubmitted for review" : "Saved") }, p.status === "rejected" ? "Save & resubmit" : "Save link"));
}

function reviewBox(p) {
  const fb = h("textarea", { placeholder: "Feedback for the student (required if rejecting)" });
  return h("div", { class: "card stack" }, h("h2", null, "Teacher review"), fb,
    h("div", { class: "row" },
      h("button", { class: "btn ok", onclick: act(() => api(`/projects/${p.id}/review`, "POST", { decision: "approve", feedback: fb.value }), "Project approved") }, "Approve"),
      h("button", { class: "btn bad", onclick: act(() => api(`/projects/${p.id}/review`, "POST", { decision: "reject", feedback: fb.value }), "Project rejected") }, "Reject")));
}

async function pageNew(ideaId) {
  if (!S.user || S.user.role !== "student") return h("div", { class: "empty" }, "Only students can create projects. ", h("a", { href: "#/login" }, "Log in as a student"));
  let idea = null;
  if (ideaId) idea = (await api("/ideas")).ideas.find(i => i.id == ideaId);
  const f = { title: h("input", { value: idea ? idea.title : "" }), summary: h("input", { value: idea ? idea.description.slice(0, 280) : "" }),
    description: h("textarea"), category: h("select", { value: idea ? idea.category : "Web" }, CATEGORIES.map(c => h("option", { value: c }, c))),
    price: h("input", { type: "number", min: 1 }), goal: h("input", { type: "number", min: 1 }) };
  if (idea) f.category.value = idea.category;
  const rows = h("div");
  const addRow = (t = "", w = 1) => { const r = h("div", { class: "mrow" }, h("input", { placeholder: "Milestone (e.g. UI design)", value: t }), h("input", { type: "number", min: 1, value: w, title: "Weight = effort" }),
    h("button", { class: "btn sm ghost", type: "button", onclick: () => r.remove() }, "x")); rows.append(r); };
  ["Requirements & design", "Core development", "Testing & documentation"].forEach(t => addRow(t, 2));
  const field = (l, el) => h("div", null, h("label", null, l), el);
  return h("form", { class: "card form stack", onsubmit: async ev => {
    ev.preventDefault();
    try {
      const milestones = [...rows.children].map(r => ({ title: r.children[0].value, weight: Number(r.children[1].value) })).filter(m => m.title.trim());
      const r = await api("/projects", "POST", { title: f.title.value, summary: f.summary.value, description: f.description.value, category: f.category.value,
        price: Number(f.price.value), goal: Number(f.goal.value), milestones, idea_id: idea ? idea.id : null });
      toast("Submitted! A teacher will review it."); location.hash = "#/project/" + r.id;
    } catch (e) { toast(e.message, true); }
  } },
    h("h2", null, idea ? "Build this idea: " + idea.title : "Submit a new project"),
    h("p", { class: "muted small" }, "A teacher must approve your project before it appears publicly. Milestones define how progress % is calculated."),
    field("Title", f.title), field("One-line summary", f.summary), field("Detailed description", f.description), field("Category", f.category),
    h("div", { class: "row" }, h("div", { style: "flex:1" }, field("Sale price (Rs)", f.price)), h("div", { style: "flex:1" }, field("Funding goal (Rs)", f.goal))),
    h("div", null, h("label", null, "Milestones (weight = how much effort it is)"), rows, h("button", { class: "btn sm ghost", type: "button", onclick: () => addRow() }, "+ Add milestone")),
    h("button", { class: "btn", type: "submit" }, "Submit for teacher review"));
}

async function pageIdeas() {
  const { ideas } = await api("/ideas");
  const wrap = h("div", { class: "stack" });
  wrap.append(h("h1", null, "Project ideas"), h("p", { class: "muted" }, "Anyone can suggest an idea. Vote for the best ones - students can pick an idea and build it."));
  if (S.user) {
    const t = h("input", { placeholder: "Idea title" }), d = h("textarea", { placeholder: "Describe the problem and what you'd like built..." });
    const c = h("select", null, CATEGORIES.map(x => h("option", { value: x }, x)));
    wrap.append(h("div", { class: "card stack" }, h("h2", null, "Suggest an idea"), t, d, c,
      h("button", { class: "btn", onclick: act(() => api("/ideas", "POST", { title: t.value, description: d.value, category: c.value }), "Idea posted") }, "Post idea")));
  } else wrap.append(h("div", { class: "notice" }, h("a", { href: "#/login" }, "Log in"), " to suggest or vote on ideas."));
  wrap.append(h("div", { class: "grid" }, ideas.map(i => h("div", { class: "card stack" },
    h("div", { class: "row between" }, h("span", { class: "pill" }, i.category), i.claimed_by ? h("span", { class: "badge b-approved" }, "Being built by " + i.claimed_by_name) : h("span", { class: "badge b-todo" }, "Open")),
    h("h3", null, i.title), h("p", { class: "muted small" }, i.description), h("p", { class: "small muted" }, "Suggested by " + i.author),
    h("div", { class: "row" },
      S.user && h("button", { class: "btn sm " + (i.voted ? "" : "ghost"), onclick: act(() => api(`/ideas/${i.id}/vote`, "POST")) }, (i.voted ? "Voted " : "Upvote ") + i.votes),
      !S.user && h("span", { class: "small" }, i.votes + " votes"),
      S.user && S.user.role === "student" && !i.claimed_by && h("a", { class: "btn sm ok", href: "#/new/" + i.id }, "Build this"))))));
  return wrap;
}

async function pageDashboard() {
  if (!S.user) return h("div", { class: "empty" }, h("a", { href: "#/login" }, "Log in"), " to see your dashboard.");
  const d = await api("/dashboard");
  const wrap = h("div", { class: "stack" }, h("h1", null, "Hello, " + S.user.name),
    h("div", { class: "stats" }, Object.entries(d.stats).map(([k, v]) => h("div", { class: "stat" }, h("b", null, k.includes("Rs") ? inr(v) : v), h("span", null, k.replace(" (Rs)", ""))))));
  const section = async (title, scope, emptyMsg) => {
    const { projects } = await api("/projects?scope=" + scope);
    wrap.append(h("h2", null, title), projects.length ? h("div", { class: "grid" }, projects.map(projectCard)) : h("div", { class: "empty card" }, emptyMsg));
  };
  if (S.user.role === "student") await section("My projects", "mine", "You have not created a project yet.");
  if (S.user.role === "customer") await section("Projects I'm monitoring", "supported", "You have not funded or bought anything yet. Browse projects to get started.");
  if (S.user.role === "teacher") {
    await section("Waiting for your approval", "pending", "Nothing to review.");
    wrap.append(h("h2", null, "Milestones waiting for verification"), h("div", { class: "card" }, d.to_verify.length
      ? d.to_verify.map(m => h("div", { class: "ms" }, h("div", null, h("b", null, m.title), h("div", { class: "muted small" }, m.project_title)), h("a", { class: "btn sm", href: "#/project/" + m.project_id }, "Open & verify")))
      : h("div", { class: "empty" }, "All caught up.")));
  }
  return wrap;
}

// ---------- router ----------
let routeSeq = 0; // only the most recent navigation may draw, so a slow page can't overwrite a newer one
async function route() {
  const mySeq = ++routeSeq;
  renderNav();
  const app = document.getElementById("app");
  const hash = location.hash.replace(/^#/, "") || "/";
  let m;
  try {
    let node;
    if (hash === "/") node = await pageHome();
    else if (hash === "/login") node = authForm(false);
    else if (hash === "/register") node = authForm(true);
    else if (hash === "/ideas") node = await pageIdeas();
    else if (hash === "/dashboard") node = await pageDashboard();
    else if (hash === "/new") node = await pageNew();
    else if ((m = hash.match(/^\/new\/(\d+)$/))) node = await pageNew(m[1]);
    else if ((m = hash.match(/^\/project\/(\d+)$/))) node = await pageProject(m[1]);
    else node = h("div", { class: "empty" }, "Page not found.");
    if (mySeq !== routeSeq) return;
    app.replaceChildren(node);
  } catch (e) {
    if (mySeq !== routeSeq) return;
    app.replaceChildren(h("div", { class: "empty card" }, e.message));
  }
  window.scrollTo(0, 0);
}
window.addEventListener("hashchange", route);
loadUser().then(route);
