---
name: card-creator
description: Make one card for a tool tab: a single, self-contained view of one piece of a tool's data, built from the @ba/tab SDK parts.
---

# Card-creator

A **card** answers one question for the department, such as "Can the motors climb the stair?", "Which design rules fail?" or "What does the current waveform look like?". A tab is a few cards in a `Grid`. Write one card per file in `cards/` once a tab has more than about three.

## Shape of a card

```tsx
import { Card, Stat, Status, Table, Picture, Plot, useData } from "@ba/tab";

export default function ErcCard() {
  const { data } = useData();
  const erc = data?.output?.erc;
  if (!erc) return <Card title="Electrical rule check" hint="Not collected yet. Press Collect again." />;
  return (
    <Card title="Electrical rule check" hint={`From ${erc.file}, checked by kicad-cli.`}>
      <Status state={erc.errors ? "bad" : erc.warnings ? "warn" : "good"}>{erc.errors} errors, {erc.warnings} warnings</Status>
      <Table columns={["Severity", "Rule", "Where"]} rows={erc.items.map((v: any) => [v.severity, v.description, v.where])} />
    </Card>
  );
}
```

## Rules

1. **One question per card, answer first.** Put the verdict (a `Status` or a big `Stat`) at the top and the details under it.
2. **Every number has a unit and a source.** The `hint` says where it came from ("From mechanics/sim/bridge.xml, 3 s simulated") and whether it's measured, from a datasheet, or estimated.
3. **Empty and failed states are designed, not left blank.** No data yet: say how to get it. Failed: show the script's own reason. Not installed: say "Not set up" and where to get it.
4. **Use the SDK parts**, which already follow the theme: flat grey, a thin outline in the department's colour on hover, no glow. Don't add shadows, gradients, fixed pixel widths or `nowrap` on long text.
5. **Fit the panel.** Cards sit in a `Grid` that wraps on narrow screens. Use `wide` for a card that needs the full width (a plot, a 3D view, a long table) and `resizable` for one the person may want taller (logs, tables, viewers).
6. **Plots:** `<Plot series={[{ name, x, y }]} xLabel="Time (s)" yLabel="Height (mm)" />`. Label both axes with units, and keep the series few enough to tell apart (at most 6).
7. **Pictures:** `<Picture src={data.images["frame_0.png"]} caption="..." />`. Captions say what is shown and when.
8. **Read-only:** a card never changes the project, the design or anything outside the tab. Its only action is `RunButton` (collect the data again), and only on the tab's main card.
9. **Plain words:** write for a person who isn't an engineer. Say "the arm hits its base in 12% of poses", not "collision ratio 0.12".
10. **Keep it small:** under about 150 lines per card. Split anything bigger.
