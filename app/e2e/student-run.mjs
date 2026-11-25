// Plays a beginner through the real app, then scores the result so runs can be compared.
//
//   npm run e2e -- --board COM9 --fusion
//
// The student only does what Headquarters tells them, through the screen it points to.
// Needs the app running. Costs real AI credits, so the spending limit is enforced here too.
import { chromium } from "playwright-core";
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const BACKEND = join(HERE, "..", "..", "backend");
const PYTHON = join(BACKEND, ".venv", "Scripts", "python.exe");
const EDGE = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const URL = process.env.BA_URL || "http://localhost:4173";
const args = process.argv.slice(2);
const flag = (name) => args.includes(`--${name}`);
const option = (name, fallback) => (args.includes(`--${name}`) ? args[args.indexOf(`--${name}`) + 1] : fallback);

const scenario = (await import(`./scenarios/${option("scenario", "6dof-arm")}.mjs`)).default;
const BOARD = option("board", "");
const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
const OUT = join(HERE, "runs", `${scenario.id}-${stamp}`);
mkdirSync(OUT, { recursive: true });
scenario.project.name = `${scenario.project.name} ${stamp.slice(5, 16)}`; // Unique, so the student can pick this run's project from the list.

const log = [];
const say = (...a) => { const line = `${new Date().toISOString().slice(11, 19)} ${a.join(" ")}`; console.log(line); log.push(line); };
const notes = { stalls: [], crashes: 0, pageErrors: [], stuckCount: 0, flash: null, serial: null, datasheetsMissing: [], steps: 0 };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await chromium.launch({ executablePath: EDGE, headless: true });
let page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
const watch = (p) => { p.on("pageerror", (e) => notes.pageErrors.push(e.message)); p.on("crash", () => { notes.crashes += 1; say("PAGE CRASHED"); }); };
watch(page);

const api = async (path, method = "GET", data) => {
  const r = await fetch(`${URL}/api${path}`, { method, headers: { "content-type": "application/json" }, body: data === undefined ? undefined : JSON.stringify(data) });
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${(await r.text()).slice(0, 300)}`);
  return r.json();
};

let projectId = "";
const P = () => `/projects/${projectId}`;

async function fresh() {
  if (page.isClosed() || notes.crashes > notes.handled) { page = await browser.newPage({ viewport: { width: 1400, height: 1000 } }); watch(page); notes.handled = notes.crashes; }
}
notes.handled = 0;

async function open(screen) {
  await fresh();
  await page.goto("about:blank"); // A change of hash alone wouldn't reload, and the project list would stay hidden.
  await page.goto(`${URL}/#Headquarters`);
  await page.locator("button", { hasText: scenario.project.name }).first().click({ timeout: 20000 });
  await page.waitForSelector(".hq-needs", { timeout: 30000 });
  await page.waitForTimeout(600);
  if (screen !== "Headquarters") {
    await page.evaluate((name) => { window.location.hash = encodeURIComponent(name); }, screen);  // The map opens each screen from the address.
    await page.waitForTimeout(2600);
  }
  await page.evaluate(() => document.querySelectorAll("details").forEach((d) => { d.open = true; })); // Folded tables hide buttons a student would unfold.
  await page.waitForTimeout(300);
}

async function waitForAgent(limitMs = 400000) {
  await page.waitForTimeout(2500);
  const end = Date.now() + limitMs;
  while (Date.now() < end && await page.locator(".agent-thinking").count()) await page.waitForTimeout(2000);
  await page.waitForTimeout(1200);
}

async function chat(text) {
  const box = page.locator(".agent-chat textarea").first();
  await box.fill(text);
  await box.press("Enter");
  await waitForAgent();
  const bubbles = page.locator(".agent-log > .bubble");
  return (await bubbles.count()) ? bubbles.last().innerText() : "";
}

async function clickAll(locatorFn, limit = 40) {
  let done = 0;
  for (; done < limit; done++) {
    const target = locatorFn().first();
    if (!(await target.count())) break;
    for (let wait = 0; wait < 8 && await target.isDisabled().catch(() => false); wait++) await page.waitForTimeout(500); // Saving the last click.
    if (await target.isDisabled().catch(() => true)) break; // An empty form's own button, not something waiting on the student.
    await target.click({ timeout: 60000 }).catch(() => undefined);
    await page.waitForTimeout(1500);
  }
  return done;
}

const told = new Set();
const once = (key) => (told.has(key) ? false : (told.add(key), true));

async function reviewQueue() {
  await open("Review queue");
  await page.waitForSelector(".reviews, .page .hint", { timeout: 30000 }).catch(() => undefined);
  const approved = await clickAll(() => page.locator(".page button", { hasText: /^(Approve|Add part)$/ }));
  say("Review queue: approved", approved);
}

async function confirmOnElectrical() {
  await open("Electrical");
  await clickAll(() => page.getByRole("button", { name: "Confirm all parts", exact: true }), 2);
  const bulk = await clickAll(() => page.locator('button[aria-label^="Confirm matched limits for"]'), 30);
  if (bulk) say("Electrical: confirmed the limits found on the page, for", bulk, "parts");
  const rows = page.locator('button[aria-label^="Show limits for"]');
  for (let i = 0; i < await rows.count(); i++) {
    await rows.nth(i).click().catch(() => undefined); // Only one part's limits show at a time.
    await page.waitForTimeout(500);
    await clickAll(() => page.locator("tr", { hasText: /p\. \d+/ }).getByRole("button", { name: "Confirm", exact: true }), 40);
  }
  say("Electrical: confirmed", await clickAll(() => page.getByRole("button", { name: "Confirm", exact: true }), 120));
}

async function noDatasheet(component, number) {
  await open("Electrical");
  page.once("dialog", (dialog) => dialog.accept("I couldn't find one for this basic part."));
  const row = page.locator("tr", { has: page.locator(`input[aria-label="Upload datasheet for ${number}"]`) }).first();
  await row.getByRole("button", { name: "No datasheet exists" }).click();
  await page.waitForTimeout(1500);
  say("Marked as having no datasheet:", number, component.id);
}

async function uploadDatasheets() {
  const state = await api(`${P()}/electrical`);
  const sheets = new Set(state.datasheets.filter((d) => (d.fields.doc ?? "datasheet") === "datasheet").map((d) => d.fields.component));
  const dir = join(OUT, "datasheets");
  mkdirSync(dir, { recursive: true });
  await open("Electrical");
  const handled = new Set();
  for (const c of state.components.filter((x) => x.status === "confirmed" && !sheets.has(x.id) && !x.fields.generic && !x.fields.no_datasheet)) {
    const number = c.fields.part_number;
    if (handled.has(number)) continue;
    handled.add(number);
    let url = scenario.datasheets.find(([re]) => re.test(number))?.[1] || c.fields.datasheet_url;
    if (!url && scenario.noDatasheet.test(number)) {
      await noDatasheet(c, number);
      continue;
    }
    if (!url && once(`ask:${number}`)) {
      const reply = await chat(`I can't find a datasheet for ${number}. Can you give me a direct link to the PDF? If there isn't one, please pick a similar part that has a datasheet.`);
      url = reply.match(/https?:\/\/[^\s)]+\.pdf/i)?.[0];
    }
    if (!url) { notes.datasheetsMissing.push(number); say("No datasheet found for", number); await noDatasheet(c, number); continue; }
    const file = join(dir, `${number.replace(/[^\w.-]+/g, "_")}.pdf`);
    const got = await fetch(url, { headers: { "user-agent": "Mozilla/5.0 BuildAnything-test" } }).catch(() => null);
    const bytes = got && got.ok ? Buffer.from(await got.arrayBuffer()) : null;
    if (!bytes || bytes.subarray(0, 5).toString() !== "%PDF-") { notes.datasheetsMissing.push(number); say("Datasheet link failed for", number); await noDatasheet(c, number); continue; }
    writeFileSync(file, bytes);
    await page.locator(`input[aria-label="Upload datasheet for ${number}"]`).first().setInputFiles(file);
    await page.waitForTimeout(5000);
    say("Uploaded datasheet for", number);
  }
  await uploadOtherDocuments();
}

// Board manuals and the like: upload what the scenario says a student would find, and say the rest doesn't exist.
async function uploadOtherDocuments() {
  const state = await api(`${P()}/electrical`);
  const dir = join(OUT, "datasheets");
  const handled = new Set();
  for (const c of state.components.filter((x) => x.status === "confirmed")) {
    const number = c.fields.part_number;
    const needed = (state.documents?.[c.id]?.missing ?? []).filter((group) => !group.includes("datasheet"));
    if (!needed.length || handled.has(number)) continue;
    handled.add(number);
    for (const group of needed) {
      const what = group.map((k) => state.docKinds?.[k] ?? k).join(" or ");
      const found = (scenario.documents ?? []).find(([re, kind]) => re.test(number) && group.includes(kind));
      if (found) {
        const [, kind, url] = found;
        const got = await fetch(url, { headers: { "user-agent": "Mozilla/5.0 BuildAnything-test" } }).catch(() => null);
        const bytes = got && got.ok ? Buffer.from(await got.arrayBuffer()) : null;
        if (bytes && bytes.subarray(0, 5).toString() === "%PDF-") {
          const file = join(dir, `${number.replace(/[^\w.-]+/g, "_")}-${kind}.pdf`);
          writeFileSync(file, bytes);
          await page.locator(`input[aria-label="Upload ${state.docKinds?.[kind] ?? kind} for ${number}"]`).first().setInputFiles(file);
          await page.waitForTimeout(5000);
          say(`Uploaded ${what} for`, number);
          continue;
        }
        say(`Link failed for the ${what} of`, number);
      }
      page.once("dialog", (dialog) => dialog.accept("I don't have this document."));
      await page.getByRole("button", { name: `No ${what} for ${number}` }).first().click();
      await page.waitForTimeout(1500);
      say(`Said there's no ${what} for`, number);
    }
  }
}

async function approveFusionChange() {
  await open("Mechanical");
  const go = page.getByRole("button", { name: "Approve and run" }).first();
  if (!(await go.count())) return false;
  await go.click();
  await page.waitForTimeout(6000);
  for (let i = 0; i < 200 && await page.locator("button", { hasText: "Approve and run" }).first().isDisabled().catch(() => false); i++) await page.waitForTimeout(2000);
  const view = await api(`${P()}/cad`);
  say("Fusion:", view.changes.filter((c) => c.stage !== "withdrawn").map((c) => `${c.title.slice(0, 36)}=${c.stage}`).join(" ; "));
  return true;
}

async function acknowledge() {
  await open("Interface");
  const view = await api(`${P()}/interfaces`);
  for (const packet of view.packets) {
    await page.locator("button", { hasText: packet.name }).first().click().catch(() => undefined);
    await page.waitForTimeout(800);
    await clickAll(() => page.getByRole("button", { name: "Acknowledge", exact: true }), 6);
  }
}

async function approveGate() {
  await open("Journey");
  await page.waitForSelector(".journey-stage", { timeout: 15000 });
  const go = page.locator(".gate-report button.primary");
  if ((await go.count()) && await go.isEnabled()) {
    say("Approving:", (await go.innerText()));
    await go.click();
    await page.waitForTimeout(3000);
    return true;
  }
  return false;
}

async function decideChanges() {
  await open("Changes");
  const cards = page.locator(".change-card").filter({ has: page.getByRole("button", { name: /^Approve change/ }) });
  for (let i = 0; i < 12 && await cards.count(); i++) {
    const card = cards.first();
    const reason = card.locator('[aria-label="Reason"]');
    if (await reason.count()) await reason.fill("Going with what Electrical and the reviewer suggested.");
    await card.getByRole("button", { name: /^Approve change/ }).click();
    await page.waitForTimeout(2500);
  }
}

async function flash() {
  const device = readdirSync(join(projectRoot(), "firmware")).find((d) => existsSync(join(projectRoot(), "firmware", d, "platformio.ini")));
  if (!device || !BOARD) { say("Skipping the flash: no board device, or no --board given."); return; }
  await open("Software");
  await page.getByRole("tab", { name: device }).first().click();
  await page.waitForTimeout(1200);
  await page.getByRole("tab", { name: "Device" }).first().click();
  await page.waitForTimeout(1200);
  await page.locator('[aria-label="Serial port"]').selectOption(BOARD);
  await page.locator("button", { hasText: "Prepare to flash" }).click();
  await page.waitForSelector(".flash-plan, .flash-panel p.error", { timeout: 420000 });
  if (await page.locator(".flash-panel p.error").count()) { notes.flash = `failed: ${await page.locator(".flash-panel p.error").innerText()}`; say("Flash:", notes.flash); return; }
  for (const box of await page.locator(".flash-plan .consent input").all()) await box.check();
  await page.locator(".flash-plan button", { hasText: /flash/i }).first().click();
  await page.waitForSelector(".flash-result, .flash-panel p.error", { timeout: 540000 });
  if (await page.locator(".flash-panel p.error").count()) notes.flash = `failed: ${await page.locator(".flash-panel p.error").innerText()}`;
  else notes.flash = (await page.locator(".flash-result p").allInnerTexts()).join(" | ").slice(0, 200);
  say("Flash:", notes.flash);
}

const projectRoot = () => join(HERE, "..", "..", "projects", projectId);

function python(script, ...more) {
  const r = spawnSync(PYTHON, [join(HERE, script), projectRoot(), ...more], { encoding: "utf-8", timeout: 120000, input: "" });
  if (r.status !== 0) { say("Python step failed:", script, (r.stderr || "").slice(-400)); return null; }
  try { return JSON.parse(r.stdout.trim().split("\n").at(-1)); } catch { say("Unreadable output:", r.stdout.slice(-300)); return null; }
}

// ---- What the student does for each thing Headquarters asks

async function recordEarlyTests() {
  if (scenario.honest) { say("HONEST MODE: not recording tests as passed on someone's word; left for Claude Code."); return false; }
  const data = await api(`${P()}/tests`);
  for (const test of data.tests.filter((x) => x.status === "confirmed" && x.automatic === "none" && x.stage !== "built" && x.state !== "passed")) {
    await api(`${P()}/tests/${test.id}/result`, "POST", { value: test.metric ? (test.min ?? test.max) : null, outcome: "passed", evidence: "I went through this on the screens and it looked right to me." });
    say("Recorded a test as a beginner would:", test.key);
  }
}

const stalled = (need) => { notes.stalls.push(need.text); say("STALLED:", need.text); };
// Anything beyond what the owner asked comes out. A cautious owner doesn't sign off added work they don't understand:
// the run stops so it can be judged (fix now, a later stage, or not needed), instead of always choosing "Fix it".
async function decideFindings(need) {
  await open("Headquarters");
  const removed = await clickAll(() => page.getByRole("button", { name: "Remove it", exact: true }), 8);
  if (removed) say("Asked for findings beyond what I asked to be removed:", removed);
  const left = await page.getByRole("button", { name: "Fix it", exact: true }).count();
  if (!left) return true;
  if (scenario.cautious) { stalled({ ...need, text: `${left} Verifier findings need a judgement: fix now, later stage, or not needed` }); return false; }
  say("Asked for findings to be fixed:", await clickAll(() => page.getByRole("button", { name: "Fix it", exact: true }), 8)); // a beginner asks for a fix rather than signing off a risk
  return true;
}
const seen = new Map();

async function act(need) {
  const count = (seen.get(need.text) ?? 0) + 1;
  seen.set(need.text, count);
  if (count > 4) { stalled(need); return false; }
  const t = need.text;
  switch (need.kind) {
    case "concept": {
      await open("Headquarters");
      const next = scenario.concept[notes.conceptIndex ?? 0];
      if (next) { notes.conceptIndex = (notes.conceptIndex ?? 0) + 1; say("Student:", next.slice(0, 70)); await chat(next); return true; }
      const approve = page.locator("button", { hasText: /^Approve (concept|anyway)$/ }).first();
      await approve.click();
      await page.waitForTimeout(3000);
      return true;
    }
    case "claims": case "plan": await reviewQueue(); return true;
    case "findings":
      await open("Headquarters");
      if (!/Decide/.test(t)) { await clickAll(() => page.getByRole("button", { name: "Mark fixed", exact: true })); return true; }
      return decideFindings(need);
    case "gate": return approveGate();
    case "question":
      await open("Review queue");
      await page.locator('[aria-label="Your answer"]').first().fill(scenario.fallback);
      await page.locator('[aria-label="Your answer"]').first().press("Enter");
      await page.waitForTimeout(3000);
      return true;
    case "failed":
      await open("Headquarters");
      await page.getByRole("button", { name: "Try again" }).first().click();
      await page.waitForTimeout(3000);
      return true;
    case "no_sources":
      await open("Headquarters");
      await page.getByRole("button", { name: "Search again" }).first().click();
      await page.waitForTimeout(3000);
      return true;
    case "bug":
    case "stuck": {
      notes.stuckCount += 1;
      const department = need.goto;
      notes.stuckBy = { ...notes.stuckBy, [department]: (notes.stuckBy?.[department] ?? 0) + 1 };
      if (notes.stuckBy[department] > 3) { stalled(need); return false; } // Rewording the same problem isn't progress.
      await open(department);
      const first = scenario.firstMessages[department];
      const reply = await chat(once(`first:${department}`) && first ? first : `Headquarters says: ${t.split(": ").slice(1).join(": ")} I don't know how to do this myself, please do it or tell me exactly what to click.`);
      say(`${department} replied:`, reply.slice(0, 160).replace(/\s+/g, " "));
      return true;
    }
    case "gate_waiting":
      if (/warning doesn't apply/.test(t)) {
        await open("Electrical");
        const handler = (dialog) => dialog.accept(dialog.defaultValue() || "Electrical explained why it does not apply here.");
        page.on("dialog", handler);
        await clickAll(() => page.getByRole("button", { name: "Accept warning", exact: true }), 12);
        page.off("dialog", handler);
      }
      else if (/Check the datasheet/.test(t)) {
        await open("Electrical");
        for (const name of (t.match(/Check the datasheet for (.+?): its exact/)?.[1] ?? "").split(", ")) {
          const row = page.locator("tr", { hasText: name }).first(); // The button for the part Headquarters named, not the first on the page.
          const read = row.getByRole("button", { name: "Read limits", exact: true });
          if (!(await read.count())) continue;
          say("Reading limits for:", name);
          await read.click();
          await page.waitForTimeout(60000);
        }
      }
      else if (/Upload (datasheets|documents)/.test(t)) await uploadDatasheets();
      else if (/device facts? the app couldn't verify/.test(t)) {
        const facts = (await api(`${P()}/electrical`)).deviceFacts.filter((f) => f.review?.verdict === "needs_you");
        for (const f of facts) await api(`/projects/${projectId}/records/${f.id}/confirm`, "POST");
        say("Checked the device facts the app couldn't verify:", facts.length);
      }
      else if (/Review \d+ (components|datasheet|connections|device facts)/.test(t)) await confirmOnElectrical();
      else if (/electrical and mechanical interfaces/.test(t)) { await open("Interface"); await clickAll(() => page.getByRole("button", { name: "Confirm", exact: true })); }
      else if (/acknowledge/.test(t)) await acknowledge();
      else if (/suggested part/.test(t) && scenario.noMechanical) {
        const records = (await api(P())).records.filter((r) => r.type === "part_proposal" && r.status === "proposed");
        for (const r of records) await api(`${P()}/part-proposals/${r.id}`, "POST", { accept: false });
        say("Dismissed suggested parts, there's nothing mechanical:", records.length);
      }
      else if (/suggested part/.test(t)) { await open("Mechanical"); await page.waitForTimeout(1500); say("Mechanical: added", await clickAll(() => page.locator(".page button:not([disabled])", { hasText: /^Add( part)?$/ }))); }
      else if (/laptop bench/.test(t)) {
        await open("Tests");
        await page.waitForSelector(".bench-card", { timeout: 30000 }).catch(() => undefined);
        await page.screenshot({ path: join(OUT, `bench-${notes.steps}.png`), fullPage: true }).catch(() => undefined);
        notes.bench = (await page.locator(".bench-summary").allInnerTexts()).join(" | ");
        say("Laptop bench:", notes.bench, "- using the results");
        await clickAll(() => page.getByRole("button", { name: "Use these results", exact: true }), 4);
      }
      else if (scenario.cautious && /Approve or reject the Fusion change|change request/.test(t)) { stalled(need); return false; } // a non-technical owner: Claude Code judges these in plain terms
      else if (/Approve or reject the Fusion change/.test(t)) await approveFusionChange();
      else if (/Keep or undo/.test(t)) { await open("Mechanical"); await page.getByRole("button", { name: "Keep change" }).first().click(); await page.waitForTimeout(4000); }
      else if (/Approve \d+ test/.test(t)) { await open("Tests"); await page.locator(".page button", { hasText: /approve all/i }).first().click(); await page.waitForTimeout(3000); }
      else if (/change request/.test(t)) await decideChanges();
      else if (/tests on the Tests screen and record/.test(t)) {
        if (scenario.honest) { stalled(need); return false; } // a person records real results
        await recordEarlyTests();
      }
      else if (/Set the parts budget/.test(t)) { await api(`${P()}/budget`, "POST", { kind: "cost", limit: scenario.budget, currency: scenario.currency }); }
      else if (/over your .* budget/.test(t)) {
        notes.overBudget = (notes.overBudget ?? 0) + 1;
        if (notes.overBudget <= 2) {
          await open("Procurement");
          const reply = await chat("thats more than I can spend, can you find cheaper options for the expensive stuff? if you counted too many of something please fix it, and tell me if anything has to change");
          say("Procurement replied:", reply.slice(0, 160).replace(/\s+/g, " "));
        }
        else if (!notes.raisedBudget) { notes.raisedBudget = true; say("Raising the budget to the most I can spend:", scenario.budgetMax); await api(`${P()}/budget`, "POST", { kind: "cost", limit: scenario.budgetMax, currency: scenario.currency }); }
        else { stalled(need); return false; }
      }
      else if (/Verifier findings/.test(t)) {
        if (!await decideFindings(need)) return false;
      }
      else if (/mark them fixed/.test(t)) {
        await open("Headquarters");
        say("Marked fixed after the department reported:", await clickAll(() => page.getByRole("button", { name: "Mark fixed", exact: true }), 8));
      }
      else if (/you'd accept/.test(t)) {
        await open("Tests");
        await page.waitForTimeout(1000);
        const boxes = page.locator('input[aria-label^="What would you accept for"]');
        for (let i = 0; i < await boxes.count(); i++) { await boxes.nth(i).fill(scenario.accept); await boxes.nth(i).press("Enter"); await page.waitForTimeout(1500); }
      }
      else { stalled(need); return false; }
      return true;
    default: stalled(need); return false;
  }
}

// ---- The run

say("Scenario:", scenario.id, "| board:", BOARD || "none", "| fusion:", flag("fusion"));
const resumeId = option("resume", "");
const created = resumeId ? await api(`/projects/${resumeId}`) : await api("/projects", "POST", scenario.project);
projectId = created.id ?? resumeId;
if (resumeId) { scenario.project.name = created.meta.name; if (!scenario.freshResume) notes.conceptIndex = scenario.concept.length; } // freshResume: a project set up by hand, still at the concept
say("Project:", projectId);
if (scenario.cadZip && !resumeId) {
  const zip = readFileSync(join(HERE, scenario.cadZip));
  const r = await fetch(`${URL}/api/projects/${projectId}/mechanics/cad?filename=ma6-arm-export.zip`, { method: "POST", body: zip });
  say("Imported the CAD model:", r.status, r.ok ? "" : (await r.text()).slice(0, 200));
}
if (scenario.simulationFirst && !resumeId) { // the owner's choice: no hardware yet, so Electrical and Procurement wait
  await api(`/projects/${projectId}/scope`, "POST", { simulation_first: true, reason: scenario.simulationFirst });
  say("Simulation first:", scenario.simulationFirst);
}
writeFileSync(join(OUT, "project.txt"), projectId);

let stopReason = "step limit";
for (; notes.steps < scenario.maxSteps; notes.steps++) {
  let hq, project;
  try {
    hq = await api(`${P()}/hq`);
    project = await api(P());
  } catch (error) { // the backend restarting for a fix shouldn't end the run
    notes.apiErrors = (notes.apiErrors ?? 0) + 1;
    if (notes.apiErrors > 30) { stopReason = "the app stopped answering"; break; }
    say("App not answering, waiting:", String(error).slice(0, 120));
    await sleep(5000); notes.steps -= 1; continue;
  }
  if (project.spent > Number(option("max-spend", scenario.maxSpendUsd))) { stopReason = `spend limit $${option("max-spend", scenario.maxSpendUsd)}`; break; }
  if (project.currentGate === "G5") { stopReason = "reached Gate 5"; break; }
  if (scenario.noMechanical && !notes.mechanicalNone && /G[2-4]/.test(project.currentGate ?? "")) {
    notes.mechanicalNone = true; // The app has no button for this yet, so this is a logged shortcut around a known gap.
    await api(`${P()}/electrical/needs/none`, "POST", { department: "Mechanical", reason: scenario.noMechanical });
    say("LOGGED GAP: no screen lets a student say a project has no mechanical parts, so the script told the app directly.");
  }
  if (scenario.ownAll) {
    const electrical = await api(`${P()}/electrical`);
    for (const c of electrical.components.filter((x) => x.status === "confirmed" && !x.fields.owned)) { await api(`${P()}/electrical/components/${c.id}/owned`, "POST"); say("Already own:", c.fields.part_number); }
  }
  if (hq.running) { await sleep(4000); notes.steps -= 1; notes.waited = (notes.waited ?? 0) + 1; if (notes.waited > 900) { stopReason = "agents never finished"; break; } continue; }
  const need = hq.needs[0];
  if (!need) { await sleep(4000); notes.idle = (notes.idle ?? 0) + 1; notes.steps -= 1; if (notes.idle > 20) { stopReason = "nothing needed and no gate ready"; break; } continue; }
  notes.idle = 0;
  say(`[${project.currentGate} $${project.spent.toFixed(2)}]`, need.kind, "->", need.goto ?? "-", "|", need.text.slice(0, 110));
  try {
    if (!(await act(need))) { stopReason = `stalled: ${need.text.slice(0, 100)}`; break; }
  } catch (error) {
    seen.set(need.text, Math.max(0, (seen.get(need.text) ?? 1) - 1)); // A slow screen isn't the app refusing.
    notes.errors = (notes.errors ?? 0) + 1;
    if (notes.errors > 12) { stopReason = "too many step errors"; break; }
    say("Step failed:", String(error).slice(0, 260));
    await page.screenshot({ path: join(OUT, `error-${notes.steps}.png`), fullPage: true }).catch(() => undefined);
  }
}
say("Stopped:", stopReason);

const project = await api(P());
if (project.currentGate === "G5" || project.currentGate === "G4") {
  await open("Mechanical");
  if (flag("fusion")) {
    const exp = page.getByRole("button", { name: "Export parts for printing" }).first();
    if (await exp.count()) { await exp.click(); await page.waitForTimeout(12000); say("Exported parts for printing."); }
  }
}
if (project.currentGate === "G5") {
  await recordEarlyTests(); // Before-buying tests are due now, and the rehearsal orders parts.
  if (flag("rebuild")) for (const [department, message] of scenario.afterBlankScreen ?? []) {
    await open(department);
    say("Student to", department + ":", message.slice(0, 90));
    say(department, "replied:", (await chat(message)).slice(0, 260).replace(/\s+/g, " "));
    if (department === "Electrical") await confirmOnElectrical();
  }
  await open("Electrical");
  const state = await api(`${P()}/electrical`);
  for (const c of state.components.filter((x) => x.status === "confirmed" && scenario.ownedBoard.test(x.fields.part_number) && !x.fields.owned)) await api(`${P()}/electrical/components/${c.id}/owned`, "POST");
  await flash().catch((e) => { notes.flash = `failed: ${String(e).slice(0, 160)}`; say("Flash failed:", notes.flash); });
  if (BOARD && notes.flash && !notes.flash.startsWith("failed")) {
    await sleep(3000);
    notes.serial = python("serial_check.py", BOARD);
    say("Packet to the real board:", JSON.stringify(notes.serial));
  }
}
await page.screenshot({ path: join(OUT, "final.png"), fullPage: true }).catch(() => undefined);
await browser.close();

// ---- Score it, and compare

const rehearsal = project.currentGate === "G5" && scenario.rehearse !== false ? python("rehearse.py") : null;
writeFileSync(join(OUT, "notes.json"), JSON.stringify({ ...notes, stopReason, rehearsal }, null, 1));
const score = python(scenario.score ?? "score.py", join(OUT, "notes.json"));
writeFileSync(join(OUT, "run.log"), log.join("\n"));
if (score) {
  writeFileSync(join(OUT, "scorecard.json"), JSON.stringify(score, null, 1));
  const baseFile = join(HERE, "baselines", `${scenario.id}.json`);
  const base = existsSync(baseFile) ? JSON.parse(readFileSync(baseFile, "utf-8")) : { checks: {} };
  const lines = [`# ${scenario.id}: ${projectId}`, "", `Stopped: ${stopReason}. AI spend: $${score.spent.toFixed(2)}. Passed ${score.passed} of ${score.total}.`, "", "| Check | First run | This run | Detail |", "|---|---|---|---|"];
  for (const [id, check] of Object.entries(score.checks)) lines.push(`| ${check.label} | ${base.checks[id] === undefined ? "n/a" : base.checks[id] ? "pass" : "FAIL"} | ${check.pass ? "pass" : "FAIL"} | ${String(check.detail).replace(/\|/g, "/").slice(0, 140)} |`);
  writeFileSync(join(OUT, "scorecard.md"), lines.join("\n") + "\n");
  console.log("\n" + lines.join("\n"));
}
console.log(`\nSaved in ${OUT}`);
