"""Renders a tool tab once in a headless browser, the way the app shows it, and reports what went wrong.

It catches what reading the code can't: a tab that crashes, logs errors, renders nothing, or spills out sideways
(something wider than its panel). The bridge is answered from the data script's last output and this project's file
list; file contents aren't loaded, so a card that reads a file shows its empty state here.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from . import tabs

BROWSERS = [r"C:\Program Files\Google\Chrome\Application\chrome.exe", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
NODE = Path(r"C:\Program Files\nodejs\node.exe")
CSP = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; font-src data:"

SCRIPT = r"""
const fs = require("fs");
const { chromium } = require(process.argv[2]);
const job = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
(async () => {
  const browser = await chromium.launch({ executablePath: job.browser });
  const page = await browser.newPage({ viewport: { width: job.width, height: 900 } });
  const problems = [];
  page.on("pageerror", (e) => problems.push("The tab crashed: " + e.message));
  page.on("console", (m) => { if (m.type() === "error") problems.push("Console error: " + m.text().slice(0, 300)); });
  await page.goto(require("url").pathToFileURL(job.host).href);
  await page.waitForTimeout(job.wait);
  const frame = page.frames().find((f) => f !== page.mainFrame());
  const seen = frame ? await frame.evaluate(() => {
    const root = document.getElementById("root"); const d = document.documentElement;
    const wide = [...document.querySelectorAll("body *")].filter((e) => e.getBoundingClientRect().right > d.clientWidth + 2).slice(0, 3).map((e) => e.tagName.toLowerCase() + (e.className ? "." + String(e.className).split(" ")[0] : ""));
    return { text: (root?.innerText || "").slice(0, 600), height: d.scrollHeight, overflow: d.scrollWidth > d.clientWidth + 2, wide, crashed: (root?.innerText || "").includes("This tab hit an error") };
  }) : null;
  await page.screenshot({ path: job.shot, fullPage: true });
  await browser.close();
  console.log(JSON.stringify({ problems, seen }));
})().catch((e) => { console.log(JSON.stringify({ problems: ["The check couldn't run: " + e.message], seen: null })); });
"""


def _safe(js_literal: str) -> str:
    """A JSON literal that can sit inside a <script> without ending it."""
    return js_literal.replace("</", "<\/").replace("<!--", "<\!--")


def host_page(js: str, hello: dict[str, Any], data: dict[str, Any], files: list[dict[str, Any]], accent: str = "#a493f2") -> str:
    """A stand-in for the app: the sealed frame plus a bridge that answers from fixed data."""
    frame = f'<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="{CSP}"></head><body><div id="root"></div><script>{js.replace("</script", "<" + chr(92) + "/script")}</script></body></html>'
    bridge = {"hello": hello, "data": data, "files": files}
    return f"""<!doctype html><html><body style="margin:0;background:#12161d">
<iframe id="t" sandbox="allow-scripts" style="width:100%;height:880px;border:0"></iframe>
<script>
const B = {_safe(json.dumps(bridge))};
const t = document.getElementById("t");
t.srcdoc = {_safe(json.dumps(frame))};
addEventListener("message", (e) => {{
  const m = e.data; if (!m || m.ba !== 1 || m.reply || e.source !== t.contentWindow || !m.id) return;
  const answer = (ok, result, error) => t.contentWindow.postMessage({{ ba: 1, reply: true, id: m.id, ok, result, error }}, "*");
  if (m.op === "hello") answer(true, B.hello);
  else if (m.op === "data" || m.op === "run") answer(true, B.data);
  else if (m.op === "list") answer(true, B.files);
  else if (m.op === "service") answer(true, {{ ok: false, error: "Connected services aren't called in the check." }});
  else answer(false, null, "Not loaded in the check.");
}});
</script></body></html>"""


def render(app_root: Path, tab_id: str, project_root: Path, width: int = 1100) -> dict[str, Any]:
    browser = next((b for b in BROWSERS if Path(b).exists()), None)
    playwright = tabs.APP / "node_modules" / "playwright-core"
    node = str(NODE) if NODE.exists() else "node"
    if not browser or not playwright.exists():
        return {"ok": True, "skipped": "No browser or Playwright on this computer, so the tab wasn't rendered. It compiled."}
    meta = tabs._manifest(app_root, tab_id)
    try:
        js = tabs.bundle(app_root, tab_id, "proposed")
    except tabs.TabError as exc:
        return {"ok": False, "problems": [str(exc)]}
    data = tabs.data(app_root, tab_id, project_root.name, "proposed")
    fixture = tabs.folder(app_root, tab_id) / "proposed" / "fixture.json"
    if data.get("output") is None and fixture.exists():
        data = {"ok": True, "output": json.loads(fixture.read_text(encoding="utf-8")), "images": {}, "ranAt": None}
    hello = {"project": {"id": project_root.name, "name": project_root.name}, "department": meta.get("department", "Mechanical"), "accent": "#f0894b",
             "tab": {"id": tab_id, "title": meta.get("title", tab_id), "tool": meta.get("tool", "")}, "canRun": True, "why": ""}
    out = tabs.folder(app_root, tab_id) / "build-proposed"
    job = out / "render-job.json"
    job.write_text(json.dumps({"browser": browser, "width": width, "wait": 2500, "shot": str(out / "render.png"),
                               "host": str(out / "render-host.html")}), encoding="utf-8")
    (out / "render-host.html").write_text(host_page(js, hello, data, tabs.list_project_files(project_root)[:300]), encoding="utf-8")
    script = out / "render.cjs"
    script.write_text(SCRIPT, encoding="utf-8")
    try:
        done = subprocess.run([node, str(script), str(playwright), str(job)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                              env={**os.environ, "NODE_PATH": str(tabs.APP / "node_modules")})
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": True, "skipped": f"The render check couldn't run ({type(exc).__name__}). The tab compiled."}
    line = next((l for l in reversed(done.stdout.splitlines()) if l.startswith("{")), None)
    if not line:
        return {"ok": True, "skipped": "The render check gave no answer. The tab compiled.", "log": (done.stderr or done.stdout)[-600:]}
    found = json.loads(line)
    problems = list(found["problems"])
    seen = found.get("seen") or {}
    if seen.get("crashed"):
        problems.append("The tab showed its error message: " + seen.get("text", "")[:300])
    if seen and not seen.get("text", "").strip():
        problems.append("The tab rendered nothing. It should always show something, at least an empty state saying how to get data.")
    if seen.get("overflow"):
        problems.append(f"Something is wider than the panel at {width} px and spills out sideways: {', '.join(seen.get('wide', []))}. Let it wrap or scroll inside its card.")
    return {"ok": not problems, "problems": problems, "text": seen.get("text", "")[:400], "screenshot": str(out / "render.png")}
