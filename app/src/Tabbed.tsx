import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Groups a screen's sections into tabs. Each section says its tab with data-tab="…"; anything without one (the chat,
 *  messages) shows on every tab. The chosen tab is remembered per screen on this computer. */
export default function Tabbed({ id, tabs, children }: { id: string; tabs: string[]; children: ReactNode }) {
  const key = `ba.tab.${id}`;
  const [active, setActive] = useState(() => { try { const saved = localStorage.getItem(key); return saved && tabs.includes(saved) ? saved : tabs[0]; } catch { return tabs[0]; } });
  const [counts, setCounts] = useState<Record<string, number>>({});
  useEffect(() => {
    if (tabs.includes(active)) return;
    try { const saved = localStorage.getItem(key); if (saved && tabs.includes(saved)) { setActive(saved); return; } } catch { /* optional */ }
    setActive(tabs[0]);
  }, [tabs.join("|")]);
  const box = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const apply = () => {
      const next: Record<string, number> = {};
      el.querySelectorAll<HTMLElement>("[data-tab]").forEach((section) => {
        const tab = section.dataset.tab ?? "";
        next[tab] = (next[tab] ?? 0) + 1;
        const show = tab === active;
        if (section.hidden === show) section.hidden = !show;
      });
      setCounts((old) => (JSON.stringify(old) === JSON.stringify(next) ? old : next));
    };
    apply();
    const watch = new MutationObserver(apply);
    watch.observe(el, { childList: true, subtree: true });
    return () => watch.disconnect();
  }, [active]);
  const choose = (tab: string) => { setActive(tab); try { localStorage.setItem(key, tab); } catch { /* optional */ } };
  return (
    <div className="tabbed">
      <div className="viewer-tabs section-tabs" role="tablist">
        {tabs.map((t) => <button key={t} role="tab" aria-selected={t === active} className={t === active ? "on" : ""} onClick={() => choose(t)}>{t}{counts[t] === undefined && t !== "Capability" ? <span className="tab-empty"> ·</span> : null}</button>)}
      </div>
      <div ref={box}>{children}</div>
    </div>
  );
}
