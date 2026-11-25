// Every section on every screen gets an expand button: cards, agent chats and the Software panes.
// Expanded, a section fills the open card; the button, Esc or the dimmed edge puts it back.
const SECTIONS = [
  ".panel-body .card",
  ".panel-body .agent-chat",
  ".panel-body .ide-side",
  ".panel-body .ide-body > :not(.pane-resize)",
  ".panel-body .hq-fold",
  ".panel-body .stage-report",
  ".panel-body .story-log",
  ".panel-body .memory-canvas",
].join(", ");

const EXPAND = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v2H7.4l3.3 3.3-1.4 1.4L6 7.4V10H4zm10 0h6v6h-2V7.4l-3.3 3.3-1.4-1.4L16.6 6H14zM4 14h2v2.6l3.3-3.3 1.4 1.4L7.4 18H10v2H4zm16 0v6h-6v-2h2.6l-3.3-3.3 1.4-1.4 3.3 3.3V14z"/></svg>';
const COLLAPSE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M10 4v6H4V8h2.6L3.3 4.7l1.4-1.4L8 6.6V4zm4 0h2v2.6l3.3-3.3 1.4 1.4L17.4 8H20v2h-6zM4 14h6v6H8v-2.6l-3.3 3.3-1.4-1.4L6.6 16H4zm10 0h6v2h-2.6l3.3 3.3-1.4 1.4-3.3-3.3V20h-2z"/></svg>';

function collapseAll() {
  document.querySelectorAll(".section-max").forEach((el) => setMax(el as HTMLElement, false));
}

function setMax(el: HTMLElement, on: boolean) {
  if (on) collapseAll();
  el.classList.toggle("section-max", on);
  document.body.classList.toggle("has-section-max", on);
  const button = el.querySelector(":scope > .expand-btn") as HTMLButtonElement | null;
  if (button) {
    button.innerHTML = on ? COLLAPSE : EXPAND;
    button.setAttribute("aria-label", on ? "Shrink this section" : "Expand this section");
    button.title = on ? "Shrink (Esc)" : "Expand";
  }
}

function decorate(root: ParentNode) {
  root.querySelectorAll(SECTIONS).forEach((node) => {
    const el = node as HTMLElement;
    if (el.querySelector(":scope > .expand-btn")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "expand-btn";
    button.innerHTML = EXPAND;
    button.title = "Expand";
    button.setAttribute("aria-label", "Expand this section");
    button.addEventListener("click", (e) => { e.stopPropagation(); e.preventDefault(); setMax(el, !el.classList.contains("section-max")); });
    el.classList.add("expandable");
    el.appendChild(button);
  });
}

/** Starts watching the page. Returns a function that stops it. */
export function watchExpandable(): () => void {
  decorate(document);
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; decorate(document); if (!document.querySelector(".section-max")) document.body.classList.remove("has-section-max"); });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  // Esc shrinks an expanded section first; only the next Esc closes the open card.
  const key = (e: KeyboardEvent) => { if (e.key === "Escape" && document.querySelector(".section-max")) { e.stopImmediatePropagation(); collapseAll(); } };
  const outside = (e: MouseEvent) => { if (document.body.classList.contains("has-section-max") && (e.target as HTMLElement).classList?.contains("section-backdrop")) collapseAll(); };
  window.addEventListener("keydown", key, true);
  window.addEventListener("click", outside, true);
  return () => { observer.disconnect(); window.removeEventListener("keydown", key, true); window.removeEventListener("click", outside, true); };
}
