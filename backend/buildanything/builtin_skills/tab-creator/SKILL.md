---
name: tab-creator
description: Make a tool tab, a TSX tab in a department's screen showing one tool's results for the open project, with an optional sandboxed data script. For any tool, such as Fusion, MuJoCo, KiCad, MATLAB or plotting.
---

# Tab-creator

A **tool tab** is a tab in one department's screen, such as Mechanical | **MuJoCo**, that shows what one tool knows about the open project. You write it, the app checks and compiles it, and the person approves it before it appears. Use this Skill with the Card-creator Skill: a tab is a few cards.

## When a tab is worth making

Make one when the person would otherwise open files or another program to see how things are going. The best cards show what the tool already produces for debugging and checking, whatever the tool is: a 3D view of a model or scene, the latest renders or screenshots, plots of values over time, rule-check or test results with where each problem is, logs and errors from the last run, and data files as tables.

The app lists likely ones in `tab_ideas`, from connections with visual or debugging actions, result files the project's programs saved, and tools departments asked for. Offer one in a line, saying which cards you'd make, and build it when the person agrees. A connection's change actions can't run from a tab, so show the files they save into the project instead.

## What a tab is made of

| File | Required | What it does |
|---|---|---|
| `Tab.tsx` | yes | `export default function Tab()`. Lays out the cards. |
| `cards/<Name>.tsx` | no | One card per file once the tab has more than about three. |
| `adapter.py` | no | The data script: `def collect(project, out, tool) -> dict`. Runs in a sandbox. |
| `README.md` | yes | For the person: what the tab shows, where the data comes from, what it can't do. |
| `fixture.json` | no | A sample of what `collect` returns, so the tab can be checked without running the script. |

The tab runs in a **sealed frame**: no network, no storage, no access to the app's own buttons. Everything comes through the SDK, `import { ... } from "@ba/tab"`:
- `useTab()`: `{ project: {id, name}, department, accent, tab, canRun, why }`
- `useData()`: `{ data: {ok, output, images, ranAt, error}, running, error, run }`. `output` is what `collect` returned, and `images` maps each picture the script saved in `out` to a data URL.
- `useFile(path)`: one project file, read-only, as `{ text }` or `{ base64 }`. `useFiles(dir, pattern)` lists files.
- Parts: `Grid`, `Card`, `Stat`, `Status`, `Table`, `Picture`, `Plot`, `Code`, `Button`, `Choice` (a dropdown for choosing among many files or runs: never a wall of buttons), `RunButton`
- `import { ModelViewer } from "@ba/model"` shows an STL file in 3D.

Imports are limited to `@ba/tab`, `@ba/model`, `react`, `three` and the tab's own files.

## Where a tab goes

Place a tab by what each department delivers, not by which tool it is:
- **Simulation** runs simulations and shows them. Put a tab there when it shows runs live or lets someone inspect a run: the simulated scene, values over time, a run's log and errors, comparisons between runs. Only when Simulation is on the project's team.
- **The department whose deliverable the results are about** gets the static results it needs to make its next decision: Mechanical the stress or deflection plots for its parts, Electrical the voltage and current plots for its circuit, Software the test and timing results for its code. These come from files already saved, such as plots, screenshots, recordings and data tables.
- **Both**, when a tool does both: one tab in Simulation for running and inspecting, and one in the department for the results that bear on its design. Each reads only its own files and says where the rest are.
- A tool a department operates for its own deliverable belongs to that department, even if it runs scripts: a CAD program's tab goes in Mechanical, because the model is the deliverable and the scripts are only a means.

Before proposing, check `project` in your input for the team: never place a tab in a department that isn't on it.

## Steps

1. **Decide what the tab shows.** Ask what the department needs from this tool to make its next decision. One tab is one tool. Name it after the tool, 2 to 24 characters, such as `MuJoCo`, `Fusion`, `KiCad checks` or `MATLAB`.
2. **Pick where the data comes from.** Use the first of these that works:
   - **Files the project already has** (exports, logs, CSVs, results). Use `useFile` and `useFiles`, and write no script.
   - **A tab serves every project that shows it, so find files by type, never by one project's names.** List with a pattern, such as `useFiles("mechanics", "**/*.urdf")` or every folder under `simulation/results/`, and pick the newest or let the person choose with `Choice`. Never hard-code a name like `robot.urdf`, `rua.xml` or `run.json` from the project you're looking at. In `reads_json`, give patterns too (`mechanics/**/*.urdf`, `simulation/results/**/*.gif`). If the app says a tab doesn't match a project's files, update the tab to find them; don't ask a department to move or rename its files to suit the tab.
   - **A Python library** (mujoco, numpy, matplotlib, csv). Write `adapter.py`. No program is needed.
   - **A program with a command line** (kicad-cli, matlab, ngspice, platformio). Write `adapter.py`, call `tool.run([...])`, and propose the tab with `exe` set to that program. The person sees and approves the exact program.
   - **A program only the person operates** (Fusion, Altium, a GUI app). The tab shows its exported files and the scripts handed to the person. It never pretends to drive the program.
3. **Read an example:** `read_example("fusion")`, `"mujoco"`, `"kicad"`, `"matlab"` or `"plot"`.
4. **Write the files** with `write_tab_file`, then run `build_tab`. It compiles the tab, checks it against the rules below, runs the data script on the open project in the sandbox (if the person allowed tab scripts), and renders the tab once to catch errors. Fix everything it reports and build again. Stop after the build is clean. Each build costs time.
5. **Propose it** with `propose_tab(tab_id, tool, department, title, summary, exe, why, reads_json)`, once per department if it goes in two places (see Where a tab goes). Then tell the person in plain words what the tab shows, where its data comes from, and that they approve it at Reception and choose which projects show it.

## Rules (each comes from something that went wrong before)

**Honesty**
- Show only what the tool or the files really say. When data is missing, say so plainly ("No simulation folder in this project yet"); don't fill in a guess.
- If the tool isn't installed, the tab says "Not set up" and how to get it. Check before telling anyone to download something: it may already be installed.
- Label estimates as estimates and give units everywhere (mm, g, V, s, °).
- A failed run shows the real reason in plain words, from the script's error ("kicad-cli didn't find board.kicad_sch"), never "An error occurred" or a vague status.

**Status and checks**
- A Check or Refresh button must really ask the tool again, not re-read an old answer.
- Status words say what the person can do: "Fusion isn't answering. Open it with the design, then press Check." Never internal phrases like "Tools unchanged".
- Show when data was collected, with `RunButton` or `ranAt`, so stale results are obvious.

**Cost and safety**
- Keep free actions (look, refresh a file view) apart from anything slow or costly. One main button: "Collect again" runs the data script. Nothing in a tab spends AI money.
- A tab never approves, installs, flashes, buys, deletes or edits the design. Those stay with the person on their own screens. A tab can only read and show.
- Data scripts write only into `out`. The project's files are read-only. Never read keys, `.env` or Git files.
- To show a simulation or run, **replay what was recorded** (positions, frames, logs) instead of re-running someone's controller code in the tab.

**Look and layout** (the app's theme)
- Flat grey cards with a thin outline that turns the department's colour on hover. No glow, no drop shadows, no gradients. The SDK parts already do this, so use them instead of styling your own.
- Nothing may go out of bounds. Use `Grid` (cards wrap to fit), let text wrap, and use no fixed widths. Wide tables scroll inside their card. Pictures use `max-width: 100%`.
- Make a card drag-resizable (`<Card resizable>`) when it holds a long log, a big table or a viewer.
- A tab opens with its most useful card first: the verdict ("3 errors, 1 warning") before the details.

**Project switching**
- The frame is rebuilt for each project, so don't cache anything across projects. Never show one project's pictures or results while another is open.

## The data script

```python
def collect(project, out, tool):
    # project: pathlib.Path of the open project (read-only)
    # out: pathlib.Path to write pictures (.png .svg .gif .jpg) and files into; pictures reach the tab as useData().data.images
    # tool: tool.run(["arg", ...], timeout=120) -> {"code", "stdout", "stderr"}; only the program the tab was approved for
    return {"summary": "...", "rows": [...]}   # JSON, under 2 MB; big data goes in files under out
```

- **No network, no other programs, no other projects.** Raise a clear exception when something is missing (`raise SystemExit("No .kicad_sch file in this project.")`). The message reaches the tab.
- Use `matplotlib` with the Agg backend for plots and save them as PNG or SVG into `out`. Or return numbers and let the tab's `Plot` draw them.
- **Keep it under three minutes.** Sample long simulations, and cap rows at a few thousand.
- Rendering with MuJoCo or OpenGL can fail on some computers. Catch it and return the numbers anyway, with `"pictures_skipped": "<reason>"`.

## Matching tools to departments

| Kind of tool | Department | Usual source | Usual cards |
|---|---|---|---|
| CAD (Fusion, FreeCAD, Onshape exports) | Mechanical | STL/STEP exports, hand-off scripts | 3D view, parts list, scripts waiting for the person |
| Physics simulation (MuJoCo, PyBullet) | Mechanical | Python library on the sim folder | verdict, frames, trajectory plot, model facts |
| Structural (PyNite, FEA results) | Mechanical | Python library or result files | max stress/deflection vs limit, plot |
| Schematic/PCB (KiCad, Altium exports) | Electrical | kicad-cli ERC/DRC JSON, or export files | violations table, counts by severity |
| Circuit simulation (ngspice, LTspice raw) | Electrical | ngspice in batch mode, or .raw/.csv | waveform plot, operating points |
| Firmware/build (PlatformIO) | Electrical or Software | build logs, size reports | flash/RAM use, warnings |
| Tests, coverage, logs | Software | result files (JUnit XML, coverage JSON) | pass/fail counts, failing tests, log |
| Data analysis and plotting (pandas, matplotlib) | the department that owns the data | CSVs and logs in the project | summary stats, plots, table |
| Numerical tools (MATLAB, Octave) | the department that owns the problem | `matlab -batch` / `octave --eval` on a project script | figures saved to `out`, numbers |
| Prices and stock (supplier exports) | Procurement | CSV exports | price table, totals in the budget currency |

For a tool not listed, use the closest row and follow the same rules.
