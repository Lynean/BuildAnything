import type { CSSProperties, ReactNode } from "react";
import { placeOf } from "./World";

// Names people and agents use for each screen. Capitalised on purpose, so everyday words don't become links.
const WORDS: [RegExp, string][] = [
  [/\bElectrical\b/, "Electrical"],
  [/\bMechanical\b/, "Mechanical"],
  [/\bSoftware\b/, "Software"],
  [/\bSimulation\b/, "Simulation"],
  [/\bProcurement\b/, "Procurement"],
  [/\bHeadquarters\b/, "Headquarters"],
  [/\bInterface\b/, "Interface"],
  [/\bJourney\b/, "Journey"],
  [/\b(?:Tests? screen|Tests|laptop bench|Laptop bench)\b/, "Tests"],
  [/\b(?:Review queue|Your desk|Changes screen|change request)\b/, "Review queue"],
  [/\b(?:Library|Sources)\b/, "Sources"],
  [/\b(?:The Ledger|Ledger|Team screen)\b/, "Team"],
  [/\bConnectors\b/, "Connectors"],
];
const ANY = new RegExp(WORDS.map(([r]) => r.source).join("|"), "g");

export function screenFor(word: string): string | null {
  return WORDS.find(([r]) => new RegExp(`^(?:${r.source})$`).test(word))?.[1] ?? null;
}

export function go(screen: string) {
  window.location.hash = encodeURIComponent(screen);
}

export function ScreenChip({ screen, children }: { screen: string; children?: ReactNode }) {
  const place = placeOf(screen);
  const color = screen === "Journey" ? "#ffd166" : place?.color ?? "#54d6c2";
  return (
    <button type="button" className="screen-chip" style={{ ["--c" as string]: color } as CSSProperties} onClick={(e) => { e.stopPropagation(); go(screen); }} title={`Open ${place?.label ?? screen}`}>
      {children ?? place?.label ?? screen}
    </button>
  );
}

/** Plain text with department and screen names turned into shortcuts. */
export function Linked({ text }: { text: string }) {
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(ANY)) {
    const screen = screenFor(m[0]);
    if (!screen || m.index === undefined) continue;
    out.push(text.slice(last, m.index), <ScreenChip key={m.index} screen={screen}>{m[0]}</ScreenChip>);
    last = m.index + m[0].length;
  }
  out.push(text.slice(last));
  return <>{out}</>;
}

/** Markdown with the same names turned into links that Rendered draws as shortcuts. Code spans are left alone. */
export function addScreenLinks(md: string): string {
  return md.split(/(`[^`]*`|\[[^\]]*\]\([^)]*\))/).map((part, i) => (i % 2 ? part : part.replace(ANY, (w) => {
    const screen = screenFor(w);
    return screen ? `[${w}](#screen:${encodeURIComponent(screen)})` : w;
  }))).join("");
}
