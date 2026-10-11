// UnderwriteIQ desk UI. No build step, no framework: the engine lives in Python,
// this file only edits a submission and renders what the API returns.

const $ = (id) => document.getElementById(id);
const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path}: ${res.status}`);
  return res.json();
};
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const money = (v) => v == null ? "n/a" : "$" + Math.round(v).toLocaleString("en-US");
const compact = (v) => {
  if (v == null) return "n/a";
  const a = Math.abs(v);
  if (a >= 1e6) return `$${(v / 1e6).toFixed(a >= 1e7 ? 1 : 2)}M`;
  if (a >= 1e3) return `$${Math.round(v / 1e3)}k`;
  return `$${Math.round(v)}`;
};
const pct = (v, d = 1) => v == null ? "n/a" : `${(v * 100).toFixed(d)}%`;
const label = (s) => String(s ?? "unknown").replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase());
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const LIMITS = [500000, 1000000, 2000000, 3000000, 5000000, 10000000];
const DEDUCTIBLES = [1000, 2500, 5000, 10000, 25000];
const STATES = ["AZ", "CA", "CO", "FL", "GA", "IL", "MI", "MN", "NC", "NY", "OH", "PA", "TX", "WA"];
const REV_MIN = 50000, REV_SPAN = 400; // log slider: $50k .. $20M
const revToSlider = (r) => Math.round(1000 * Math.log(Math.max(r, REV_MIN) / REV_MIN) / Math.log(REV_SPAN));
const sliderToRev = (v) => Math.round(REV_MIN * REV_SPAN ** (v / 1000) / 1000) * 1000;

let meta, queue = [], current = null, lastDecision = null;

// ---------------------------------------------------------------- tooltip
const tip = $("tip");
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest("[data-tip]");
  if (!el) return tip.classList.remove("on");
  tip.textContent = el.dataset.tip;
  tip.classList.add("on");
  const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8);
  tip.style.left = `${x}px`;
  tip.style.top = `${e.clientY + 16}px`;
});

// ---------------------------------------------------------------- tabs
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
function showView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.setAttribute("aria-selected", t.dataset.view === view));
  for (const v of ["desk", "portfolio", "simulator"]) $(`view-${v}`).hidden = v !== view;
  if (view === "portfolio") loadPortfolio();
  if (view === "simulator") runSim();
  history.replaceState(null, "", `#${view}`);
}

// ---------------------------------------------------------------- desk: form
function fillSelect(el, values, fmt = label) {
  el.innerHTML = values.map((v) => `<option value="${esc(v)}">${esc(fmt(v))}</option>`).join("");
}

function initForm() {
  fillSelect($("f-class"), Object.keys(meta.classes));
  fillSelect($("f-line"), meta.lines);
  fillSelect($("f-state"), STATES, (s) => s);
  fillSelect($("f-deductible"), DEDUCTIBLES, money);
  $("f-limit").innerHTML = LIMITS.map((l) => `<button type="button" data-v="${l}">${compact(l)}</button>`).join("");
  $("f-limit").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    current.coverage_requested.limit = +b.dataset.v; syncForm(); assessSoon();
  });

  const bind = (id, apply) => $(id).addEventListener("input", (e) => { apply(e.target); syncForm(); assessSoon(); });
  bind("f-name", (t) => (current.applicant.name = t.value));
  bind("f-class", (t) => (current.applicant.business_type = t.value));
  bind("f-state", (t) => (current.applicant.state = t.value));
  bind("f-years", (t) => (current.applicant.years_in_business = +t.value));
  bind("f-revenue", (t) => (current.applicant.annual_revenue = sliderToRev(+t.value)));
  bind("f-employees", (t) => (current.applicant.employees = +t.value || 1));
  bind("f-deductible", (t) => (current.coverage_requested.deductible = +t.value));
  bind("f-construction", (t) => (current.property_details.construction_class = t.value));
  bind("f-sprinklered", (t) => (current.property_details.sprinklered = t.checked));
  bind("f-line", (t) => {
    current.coverage_requested.line_of_business = t.value;
    if (t.value === "commercial_property" && current.property_details.sprinklered === undefined)
      current.property_details = { construction_class: "masonry", sprinklered: true };
  });

  $("f-losses").addEventListener("input", (e) => {
    const tr = e.target.closest("tr"); if (!tr) return;
    const run = current.loss_runs[+tr.dataset.i];
    const k = e.target.dataset.k;
    run[k] = k === "description" ? e.target.value : (e.target.value === "" ? null : +e.target.value);
    assessSoon();
  });
  $("f-losses").addEventListener("click", (e) => {
    if (!e.target.classList.contains("x")) return;
    current.loss_runs.splice(+e.target.closest("tr").dataset.i, 1); renderLosses(); assessSoon();
  });
  $("add-loss").addEventListener("click", () => {
    current.loss_runs.push({ year: 2026, loss_amount: 25000, description: "reported claim" });
    renderLosses(); assessSoon();
  });
}

function syncForm() {
  const a = current.applicant, c = current.coverage_requested, p = current.property_details;
  if (document.activeElement !== $("f-name")) $("f-name").value = a.name ?? current.submission_id;
  $("f-sid").textContent = current.submission_id;
  $("f-class").value = a.business_type ?? "";
  $("f-state").value = a.state ?? "";
  $("f-years").value = a.years_in_business ?? 0;
  $("o-years").textContent = a.years_in_business ?? "missing";
  $("f-revenue").value = revToSlider(a.annual_revenue ?? REV_MIN);
  $("o-revenue").textContent = typeof a.annual_revenue === "number" ? money(a.annual_revenue) : "missing";
  $("f-employees").value = a.employees ?? "";
  $("f-line").value = c.line_of_business ?? "";
  $("f-deductible").value = c.deductible ?? "";
  $("f-limit").querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", +b.dataset.v === c.limit));
  $("property-set").hidden = c.line_of_business !== "commercial_property";
  $("f-construction").value = p.construction_class ?? "masonry";
  $("f-sprinklered").checked = p.sprinklered !== false;
}

function renderLosses() {
  $("f-losses").innerHTML = current.loss_runs.map((r, i) => `
    <tr data-i="${i}">
      <td><input class="yr" data-k="year" type="number" value="${esc(r.year ?? "")}" aria-label="Loss year"></td>
      <td><input class="amt" data-k="loss_amount" type="number" step="1000" value="${esc(r.loss_amount ?? "")}" aria-label="Incurred amount"></td>
      <td><input data-k="description" value="${esc(r.description ?? "")}" aria-label="Description"></td>
      <td><button type="button" class="x" aria-label="Remove claim">×</button></td>
    </tr>`).join("") || `<tr><td colspan="4" class="muted" style="padding:8px 6px">No losses reported.</td></tr>`;
}

function load(raw) {
  current = structuredClone(raw);
  current.property_details ??= {};
  current.loss_runs ??= [];
  document.querySelectorAll("#queue button").forEach((b) => b.setAttribute("aria-current", b.dataset.id === raw.submission_id));
  syncForm(); renderLosses(); clearMemo(); assess();
}

// ---------------------------------------------------------------- desk: decision
async function assess() {
  const sent = current;
  const r = await api("/api/assess", current);
  if (sent !== current) return; // a newer submission was loaded meanwhile
  renderDecision(r);
}
const assessSoon = debounce(() => { clearMemo(); assess(); }, 120);

function renderDecision(r) {
  const stamp = $("stamp");
  stamp.textContent = r.recommendation;
  stamp.className = `stamp ${r.recommendation}`;
  if (lastDecision !== r.recommendation) { void stamp.offsetWidth; stamp.classList.add("thunk"); }
  lastDecision = r.recommendation;

  $("score").textContent = r.risk_score.toFixed(0);
  $("score-fill").style.width = `${r.risk_score}%`;
  $("tick").style.left = $("tick-label").style.left = `${meta.refer_threshold}%`;
  $("premium").textContent = money(r.premium);
  $("lossprob").textContent = pct(r.loss_probability);
  $("lossprob-note").textContent = r.loss_probability == null
    ? "Model can't score incomplete data."
    : `Book average ${pct(meta.model.base_rate)}. Model signal only; it never changes the decision.`;

  const steps = r.rating.steps;
  $("worksheet").innerHTML = steps.length ? `<h3>Rating worksheet</h3>` + steps.map((s, i) => `
    <div class="${i === steps.length - 1 ? "total" : ""}"><span>${esc(s.label)}</span><span class="lead"></span>
    <span>${i === 0 || i === steps.length - 1 ? money(s.value) : "× " + s.value.toFixed(3)}</span></div>`).join("")
    : `<h3>Rating worksheet</h3><p class="muted" style="margin:0">Can't rate: ${esc(r.rating.reason)}.</p>`;

  const v = r.validation;
  const issues = [...v.missing_fields.map((f) => `Missing ${f}`), ...v.invalid_fields.map((f) => `Invalid ${f}`)];
  $("dq").innerHTML = (issues.length
    ? `<h3>Data quality</h3><ul class="bad">${issues.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>`
    : `<span class="ok">✓ Data complete</span>`) +
    (v.warnings.length ? `<ul class="warn">${v.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul>` : "");

  const op = { gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=", neq: "≠", in: "in", not_in: "not in" };
  const fmtVal = (x) => Array.isArray(x) ? `[${x.length} values]` : typeof x === "number" && x >= 10000 ? compact(x) : typeof x === "string" ? x : JSON.stringify(x);
  $("trace").innerHTML = r.rules.map((rule) => `
    <tr class="${rule.matched ? "matched hit " + rule.outcome : ""}">
      <td>${rule.rule_id}</td>
      <td class="desc">${esc(rule.description)}</td>
      <td data-tip="${esc(rule.field)}">${esc(rule.field.split(".").pop())} ${op[rule.operator]} ${esc(fmtVal(rule.threshold))}</td>
      <td>${rule.observed == null ? "—" : esc(fmtVal(rule.observed))}</td>
      <td>${rule.matched ? `<span class="hit ${rule.outcome}">HIT ${rule.outcome} +${rule.weight}</span>` : `<span class="pass">pass</span>`}</td>
    </tr>`).join("");
}

// ---------------------------------------------------------------- desk: memo
const CHECK_NAMES = {
  decision_echo: "Restates the engine's decision",
  no_fabrication: "Cites only rules that fired",
  coverage: "Cites every rule that fired",
  missing_data: "Asks the broker for all missing data",
  llm_unavailable: "Claude unavailable, used template",
  llm_draft_rejected: "Claude draft rejected by guardrails",
};
function clearMemo() {
  $("memo-body").innerHTML = `<p class="memo-empty">Draft a memo to get a plain-English summary of this decision for the file. It cites the rules above, and guardrails check it against the engine before you see it.</p>`;
  $("checks").innerHTML = "";
}
$("draft").addEventListener("click", async () => {
  const btn = $("draft");
  btn.disabled = true; btn.textContent = "Drafting…";
  try {
    const r = await api("/api/memo", current);
    const m = r.memo;
    const src = r.source === "claude" ? `Drafted by Claude (${esc(meta.llm_model ?? "claude-opus-5-5")}), verified` : `Deterministic template draft, verified${r.source.includes("fallback") ? " (Claude fallback)" : ""}`;
    $("memo-body").innerHTML = `
      <div class="source">${src}</div>
      <p>${esc(m.summary)}</p>
      ${m.drivers.length ? `<h3>Drivers</h3><ul>${m.drivers.map((d) => `<li><b class="num">${esc(d.rule_id)}</b> ${esc(d.explanation)}</li>`).join("")}</ul>` : ""}
      ${m.broker_requests.length ? `<h3>Ask the broker for</h3><ul>${m.broker_requests.map((b) => `<li>${esc(b)}</li>`).join("")}</ul>` : ""}
      <h3>For the reviewing underwriter</h3><p>${esc(m.reviewer_question)}</p>
      ${m.ambiguity_flag ? `<p class="hit refer">Near-threshold or malformed data present. Human review required.</p>` : ""}`;
    $("checks").innerHTML = `<li style="border:0;padding-top:12px"><b>Guardrails</b></li>` + r.checks.map((c) => `
      <li class="${c.passed ? "pass" : "fail"}"><span class="mark">${c.passed ? "✓" : "✗"}</span>
      <span>${esc(CHECK_NAMES[c.name] ?? c.name)}${c.detail && (!c.passed || c.name.startsWith("llm_")) ? `<small>${esc(c.detail)}</small>` : ""}</span></li>`).join("");
  } finally {
    btn.disabled = false; btn.textContent = "Draft memo";
  }
});

// ---------------------------------------------------------------- portfolio
const DCOL = { accept: "var(--accept)", refer: "var(--refer)", decline: "var(--decline)" };
function stackBar(name, counts, total) {
  const seg = ["accept", "refer", "decline"].filter((k) => counts[k]).map((k) => {
    const share = counts[k] / total;
    return `<div style="flex:${counts[k]};background:${DCOL[k]}" data-tip="${label(k)}: ${counts[k].toLocaleString()} (${pct(share)})">${share > 0.09 ? pct(share, 0) : ""}</div>`;
  }).join("");
  return `<div class="bar-row"><span class="name">${esc(name)}</span><div class="stack">${seg}</div><span class="val">${total.toLocaleString()}</span></div>`;
}

let portfolioLoaded = false;
async function loadPortfolio() {
  if (portfolioLoaded) return;
  const p = await api("/api/portfolio");
  portfolioLoaded = true;
  const k = p.kpis;
  const prem = p.bound_book.reduce((s, r) => s + r.written_premium, 0);
  const inc = p.bound_book.reduce((s, r) => s + r.incurred_loss, 0);
  const dq = p.dq_issues.reduce((s, r) => s + r.occurrences, 0);
  $("tiles").innerHTML = [
    [k.submissions.toLocaleString(), "Submissions assessed"],
    [pct(k.accepted / k.submissions), "Auto-accept rate"],
    [compact(prem), "Bound premium"],
    [pct(inc / prem), "Bound loss ratio"],
    [dq.toLocaleString(), "Data quality issues"],
  ].map(([v, l]) => `<div class="tile"><div class="v">${v}</div><div class="l">${l}</div></div>`).join("");

  const lines = {};
  for (const r of p.decision_mix) (lines[r.line_of_business] ??= {})[r.recommendation] = r.submissions;
  $("mix").innerHTML = Object.entries(lines).map(([line, c]) =>
    stackBar(label(line), c, Object.values(c).reduce((a, b) => a + b, 0))).join("");

  const maxHits = Math.max(...p.rule_hits.map((r) => r.hits));
  $("hits").innerHTML = p.rule_hits.map((r) => `
    <div class="bar-row wide"><span class="name" data-tip="${esc(r.description)}">${r.rule_id} ${esc(r.description)}</span>
    <div class="single"><div style="width:${100 * r.hits / maxHits}%" data-tip="${r.hits} hits, ${r.hit_rate_pct}% of submissions"></div></div>
    <span class="val">${r.hit_rate_pct.toFixed(1)}%</span></div>`).join("");

  $("bound").innerHTML = `<tr><th>Line</th><th>Policies</th><th>Premium</th><th>Incurred</th><th>Loss ratio</th></tr>` +
    p.bound_book.map((r) => `<tr><td>${label(r.line_of_business)}</td><td>${r.policies}</td><td>${compact(r.written_premium)}</td><td>${compact(r.incurred_loss)}</td><td>${pct(r.loss_ratio)}</td></tr>`).join("");

  $("dqtable").innerHTML = `<tr><th>Field</th><th>Problem</th><th>Count</th></tr>` +
    p.dq_issues.map((r) => `<tr><td class="num" style="font-family:var(--mono);font-size:12.5px">${esc(r.field)}</td><td>${r.severity}</td><td>${r.occurrences}</td></tr>`).join("");

  $("runs").innerHTML = `<tr><th>Run</th><th>Rows</th><th>Assessed</th><th>Flagged</th><th>Ruleset</th><th>Time</th></tr>` +
    p.runs.map((r) => `<tr><td>#${r.run_id} ${esc(r.source)}</td><td>${r.rows_in}</td><td>${r.rows_assessed}</td><td>${r.rows_dq_flagged}</td><td>${esc(r.ruleset_version)}</td><td>${r.duration_ms} ms</td></tr>`).join("");

  const m = meta.model;
  $("model-summary").textContent = `Logistic regression on ${m.train_rows.toLocaleString()} submissions, tested on ${m.test_rows.toLocaleString()} held out. ` +
    `Test AUC ${m.test_auc.toFixed(3)}, Brier ${m.test_brier.toFixed(4)}, base claim rate ${pct(m.base_rate)}. Standardised coefficients:`;
  const coefs = Object.entries(m.coefficients).filter(([n]) => n !== "intercept");
  const maxC = Math.max(...coefs.map(([, v]) => Math.abs(v)));
  $("coef").innerHTML = coefs.map(([n, v]) => `
    <span>${label(n)}</span>
    <div class="neg">${v < 0 ? `<div style="width:${100 * -v / maxC}%" data-tip="Lowers claim odds"></div>` : ""}</div>
    <div class="pos">${v > 0 ? `<div style="width:${100 * v / maxC}%" data-tip="Raises claim odds"></div>` : ""}</div>
    <span class="v">${v > 0 ? "+" : ""}${v.toFixed(3)}</span>`).join("");
}

// ---------------------------------------------------------------- simulator
let overrides = {}, threshold;
const NUMERIC_RANGES = {
  "GL-002": [0, 10, 1, (v) => `${v} yrs`],
  "GL-003": [1000000, 20000000, 500000, compact],
  "GL-004": [0, 5, 1, (v) => `${v} losses`],
  "GL-006": [10000, 500000, 10000, compact],
  "GL-008": [1, 20, 0.5, (v) => `${v}× rev`],
};

function initSim() {
  threshold = meta.refer_threshold;
  $("s-threshold").value = threshold; $("o-threshold").textContent = threshold;
  $("s-threshold").addEventListener("input", (e) => { threshold = +e.target.value; $("o-threshold").textContent = threshold; runSimSoon(); });

  $("rule-ctls").innerHTML = meta.rules.map((r) => {
    const rng = NUMERIC_RANGES[r.rule_id];
    return `<div class="rule-ctl" data-id="${r.rule_id}">
      <label class="head"><input type="checkbox" checked data-k="enabled">
        <span><span class="rid">${r.rule_id}</span>${esc(r.description)}</span></label>
      <div class="ctl">
        ${rng ? `<input type="range" data-k="value" min="${rng[0]}" max="${rng[1]}" step="${rng[2]}" value="${r.value}" aria-label="Threshold"><output>${rng[3](r.value)}</output>` : `<span class="muted" style="flex:1">${Array.isArray(r.value) ? r.value.map(label).join(", ") : esc(JSON.stringify(r.value))}</span>`}
        <select data-k="outcome" aria-label="Outcome">${["refer", "decline", "surcharge"].map((o) => `<option ${o === r.outcome ? "selected" : ""}>${o}</option>`).join("")}</select>
      </div></div>`;
  }).join("");

  $("rule-ctls").addEventListener("input", (e) => {
    const box = e.target.closest(".rule-ctl"), id = box.dataset.id, k = e.target.dataset.k;
    const o = (overrides[id] ??= {});
    if (k === "enabled") { o.enabled = e.target.checked; box.classList.toggle("off", !e.target.checked); }
    if (k === "value") { o.value = +e.target.value; box.querySelector("output").textContent = NUMERIC_RANGES[id][3](o.value); }
    if (k === "outcome") o.outcome = e.target.value;
    runSimSoon();
  });

  document.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => applyPreset(b.dataset.preset)));
}

function applyPreset(name) {
  const presets = {
    reset: [{}, meta.refer_threshold],
    authority: [{ "GL-003": { value: 10000000 } }, meta.refer_threshold],
    tighten: [{ "GL-002": { value: 4, outcome: "decline" } }, meta.refer_threshold],
    open: [{ "GL-001": { enabled: false }, "GL-002": { enabled: false }, "GL-003": { value: 20000000 }, "GL-007": { enabled: false } }, 70],
  };
  [overrides, threshold] = structuredClone(presets[name]);
  $("s-threshold").value = threshold; $("o-threshold").textContent = threshold;
  document.querySelectorAll(".rule-ctl[data-id]").forEach((box) => {
    const r = meta.rules.find((x) => x.rule_id === box.dataset.id), o = overrides[r.rule_id] ?? {};
    const on = o.enabled !== false;
    box.querySelector("[data-k=enabled]").checked = on; box.classList.toggle("off", !on);
    box.querySelector("[data-k=outcome]").value = o.outcome ?? r.outcome;
    const range = box.querySelector("[data-k=value]");
    if (range) { range.value = o.value ?? r.value; box.querySelector("output").textContent = NUMERIC_RANGES[r.rule_id][3](+range.value); }
  });
  runSim();
}

async function runSim() {
  const r = await api("/api/simulate", { overrides, refer_threshold: threshold });
  const b = r.baseline.total, s = r.scenario.total;
  const delta = (sv, bv, fmt, goodWhenUp) => {
    const d = sv - bv;
    if (Math.abs(d) < 1e-9) return `<span class="delta flat">no change</span>`;
    const good = goodWhenUp ? d > 0 : d < 0;
    return `<span class="delta ${good ? "good" : "bad"}">${d > 0 ? "▲" : "▼"} ${fmt(Math.abs(d))}</span>`;
  };
  const pts = (v) => `${(v * 100).toFixed(1)} pts`;
  $("compare").innerHTML = [
    ["Auto-accept rate", pct(s.accept_rate), pct(b.accept_rate), delta(s.accept_rate, b.accept_rate, pts, true)],
    ["Bound premium", compact(s.written_premium), compact(b.written_premium), delta(s.written_premium, b.written_premium, compact, true)],
    ["Loss ratio", pct(s.loss_ratio), pct(b.loss_ratio), delta(s.loss_ratio, b.loss_ratio, pts, false)],
    ["Underwriting profit", compact(s.underwriting_profit), compact(b.underwriting_profit), delta(s.underwriting_profit, b.underwriting_profit, compact, true)],
  ].map(([l, v, base, d]) => `<div><div class="l">${l}</div><div class="v">${v}</div><div class="b">baseline ${base}</div>${d}</div>`).join("");
  $("sim-version").textContent = `Scenario ruleset ${r.ruleset_version}, ${s.refer.toLocaleString()} files sent to the referral queue (baseline ${b.refer.toLocaleString()}). Profit assumes a 30% expense ratio.`;

  $("sim-mix").innerHTML = stackBar("Baseline", b, b.submissions) + stackBar("Scenario", s, s.submissions);
  $("sim-lines").innerHTML = `<tr><th>Line</th><th>Accept rate</th><th>Premium</th><th>Loss ratio</th><th>Baseline loss ratio</th></tr>` +
    Object.entries(r.scenario.by_line).map(([line, x]) => `<tr><td>${label(line)}</td><td>${pct(x.accept_rate)}</td><td>${compact(x.written_premium)}</td><td>${pct(x.loss_ratio)}</td><td>${pct(r.baseline.by_line[line]?.loss_ratio)}</td></tr>`).join("");
}
const runSimSoon = debounce(runSim, 80);

// ---------------------------------------------------------------- boot
(async function boot() {
  [meta, queue] = await Promise.all([api("/api/meta"), api("/api/queue")]);
  $("version").textContent = `v${meta.version}`;
  $("provenance").innerHTML = `Synthetic book of <b>${meta.book_size.toLocaleString()}</b> submissions<br>Ruleset <b class="num">${meta.ruleset_version}</b>`;
  $("queue").innerHTML = queue.map((q) => `
    <li><button type="button" data-id="${esc(q.submission_id)}">
      <span class="who">${esc(q.applicant.name ?? q.submission_id)}</span>
      <span class="what">${esc(label(q.applicant.business_type ?? "class missing"))}, ${esc(label(q.coverage_requested.line_of_business))}, ${compact(typeof q.coverage_requested.limit === "number" ? q.coverage_requested.limit : null)}</span>
    </button></li>`).join("");
  $("queue").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (b) load(queue.find((q) => q.submission_id === b.dataset.id));
  });
  initForm(); initSim();
  load(queue[0]);
  const view = location.hash.slice(1);
  if (["portfolio", "simulator"].includes(view)) showView(view);
})();
