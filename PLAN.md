# BuildAnything: Project Plan

An agentic platform that takes a physical product from idea to working prototype, using a headquarters team and Mechanical, Electrical, Software and Procurement departments.

Status: proposal, revision 4, 2026-11-08. Nothing is built yet. Time estimates assume one developer working close to full time, and they are rough.

**What changed in revision 4:** the app now works with any AI provider through a key you add, starting with OpenAI (section 8). Agents use model tiers instead of named models, and costs use OpenAI prices (section 10).

**Revision 3:** your past projects became a record of what went wrong, with each failure turned into a platform requirement (section 2). The AI spending limit is $20 per project.

---

## 1. The main design decision

Many agents talking to each other will not stay aligned. Prompts telling them "don't hallucinate" will not hold up over a months-long project.

So the product's core is a **Project Ledger**, a typed, versioned record of everything the project has established: requirements, sources, components, constraints, interfaces, decisions, tasks, orders. Agents are stateless workers that read a scoped slice of it and propose changes. Conversations are disposable and the ledger is the truth.

Three things follow from this:

- **Alignment is structural.** An agent can only cite a source that exists in the ledger, and can only use a component parameter that was extracted from an uploaded datasheet.
- **Need-to-know is a query.** Electrical's briefing is generated from the ledger, so it stays current and small. Small briefings are also what makes the budget workable.
- **Change is traceable.** If a battery swap raises mass by 80 g, the dependency graph shows which calculations, tasks and drawings are now stale.

The second differentiator is **deterministic checking**. Wherever a rule can be expressed in code (voltage compatibility, power budgets, ERC/DRC, compile, unit consistency, CAD clearance), code decides and the LLM only explains.

## 2. What went wrong in past projects, and what the platform must do

These projects exist to show what the platform must fix. They are not patterns to copy. I reviewed folder contents, Git history, READMEs, status files, scripts and extracted PDF text. I did not open CAD models, and "not found" below means not found by file name or targeted search.

### Stair climber (RB4107)

| What went wrong | Evidence | Platform requirement |
|---|---|---|
| Cost arrived after the design | Week 11 update: aluminium parts redesigned as printed or acrylic "due to budget constraint", after fabrication packs existed | Cost is a budget from G2, with a live estimate. A design over budget cannot pass review. |
| A material change was checked for fit only | Deck P12 went from Al 6061-T6 to cast acrylic. The change record notes "clash with nothing"; no strength recheck was found, and no calculation files were found | Each kind of change invalidates specific checks. A material change reopens the strength and mass checks (section 6). |
| Requirements did not reach the design | Load and range are in the proposal PDF. "Robot mass target 3 kg" first appears in a Rev C1 README box | Every requirement and target lives in the ledger and is linked to the parts and tests that satisfy it. |
| Revision history lived in layered text | README stacks C2 over C1 over C, with "this box wins". It names a `Newest.zip`, while the pack root has `Finalized.zip` | One record per part and revision. READMEs are generated. |
| Changes continued after sending to fabrication | `Fabrication request as sent 26 Oct`, followed by changes on 29 Oct and 3 Nov | A Release is a frozen snapshot. Changing released parts shows what was already sent and needs a decision. |
| Obsolete files stayed in current folders | `3 STEP 3D models` still contains P24 at 10x10x1.5, though C2 specifies 20x20x1 | Packs are exported from the ledger, so obsolete parts can't appear. |
| CAD editing was uncontrolled trial and error | About 70 scripts and result files for the deck and P24 on 3 Nov alone, including repeated undo attempts. No Git repository | CAD transactions (section 8): checkpoint, change, verify, then keep or roll back. |
| Purchases were reconstructed afterwards | S$1,100.36 assembled from invoices and screenshots: four buyer names, one screenshot showing 4 of 8 items, unknown currency, duties not included | Orders are logged when placed, with buyer, funding source, landed cost and missing-data flags. |

### Drone (`esp-everything-qgc-bridge` and frame design)

| What went wrong | Evidence | Platform requirement |
|---|---|---|
| Interfaces were synchronized by hand | At least six "must match" comments between C and Python, and a separate `struct` format string | Interfaces are defined once and code is generated. |
| A core function was switched off without a record | `TOF_ENABLED 0` in firmware, while `CLAUDE.md` still documents the 8-sensor obstacle pipeline as the architecture | A Deviation record states what is disabled, why, which requirement it affects, and what restores it. Docs are generated from it. |
| Disciplines were disconnected | SolidWorks frame, MATLAB controllers and firmware are separate. No mass, thrust or battery figures in the README or fleet config | Shared mass, inertia and power budgets feed all departments. |
| No electrical record | No datasheets, schematic or BOM found | Unconfirmed components block the design review. |
| Tests don't trace to requirements | Laptop-side tests exist, none found for firmware. Windows setup is marked "Haven't tested yet" | Each requirement has a verification method and result. Untested setups are shown as untested. |

### 3Birds

| What went wrong | Evidence | Platform requirement |
|---|---|---|
| Project knowledge was deleted in one commit | "Delete the md files" removed 12 documents and 1,447 lines, including the Master Spec, Implementation Guide and all STATUS files. README still links to them | Knowledge is append-only and can't be removed by a file cleanup. Broken references are flagged. |
| Each person's agent had different instructions | `.gitignore` excludes `CLAUDE.md`, `AGENTS.md` and a handoff file. `ARCHITECTURE.md` is untracked, with 32 uncommitted changes | Agent instructions come from shared project state. |
| People manually carried messages between agents | Long status files written for the other person's session to read | Departments share the ledger, and handoffs are only needed between people. |
| Contracts drifted | Trust range was 0–5 in one status file and 0–3 later that day. Discovered task types existed in only one team's table | Versioned contracts. A breaking change requires each consumer to acknowledge it. One owner for each fact. |
| Architecture sprawled | Next.js, Vite and Cloudflare Workers tooling in one frontend, two lockfiles, several separate frontends | Technology choices are Decisions, and additions need a Change Request. |
| Almost no verification | One test file among 93 tracked files, and 62 commits in two days | Done means verified, with evidence attached. |

### Common root causes

1. **Nothing held the truth**, so it spread across READMEs, folder names, comments, PDFs and chat.
2. **Changes didn't trigger re-verification.**
3. **Constraints arrived late**, especially cost.
4. **Agents worked fast with no process around them**, so speed produced clutter.
5. **Records were written after the work,** or not at all.

### Worth keeping on their own merits

Export checks against the model, staged hardware tests with typed arming, a compile-time packet size check, limits enforced in the database as well as in prompts, and credentials passed outside documents.

## 3. Changes from your original brief

| Your brief | What I changed | Why |
|---|---|---|
| Concept agent has "low temperature (creative)" | Dropped temperature as the mechanism | Low temperature means *less* random, and the setting isn't available on every current model, so it can't work across providers. Agents differ by prompt, permissions, model tier, effort level and what checks their output. |
| "Literally anything" | First release targets electromechanical, IoT and robotics prototypes | That is your real experience, and it gives concrete test projects. |
| "Make sure they don't hallucinate" | Six enforcement layers (section 6), plus a new independent Verifier agent | An agent reviewing its own work shares its own blind spots. |
| Software starts "when things are going quite well" | Software starts when the **interface contract is frozen** | This makes the vague trigger checkable, and it can start earlier against simulated devices. |
| Departments update HQ and HQ informs others | Kept, implemented as events routed by the Controller | No peer-to-peer agent chat. Every cross-department fact passes through the ledger. |
| "Electrical doesn't need to know mechanical" | Isolate by department, but share **interfaces** | Electrical does need the mass limit, enclosure volume, and connector locations. It doesn't need the linkage analysis. |

I read "anything have to have a ground and have a backup source" as: every piece of guidance must be grounded in a source and have a fallback. Section 7 implements that.

## 4. Agent roster

Start with one lead agent per department that loads skills on demand. Split a role into a separate agent only when tests show a benefit, because each split adds cost and coordination.

### Headquarters (four agents)

| Agent | Job | Can write to | Model tier (section 8) |
|---|---|---|---|
| **Concept Partner** | Explores ideas with you, explains trade-offs, challenges assumptions | Drafts only; marked speculative | Standard |
| **Research Engineer** | Finds papers and open-source work, extracts claims with citations | Sources, Claims | Deep for synthesis, Fast for screening |
| **Project Controller** | Owns the task graph, timeline and budgets, writes briefings and handoffs, routes updates, detects drift | Tasks, Briefings, Change Requests | Standard |
| **Verifier** (new) | Independently checks deliverables against requirements and sources; sees the output but not the author's reasoning | Review findings only | Deep, at gates only |

Department leads default to Standard, and bulk reading uses Fast.

The Concept Partner is deliberately walled off: nothing it says becomes project truth until the Research Engineer has sourced it and you have approved it.

### Departments

| Department | Responsibilities | Capabilities needed (section 8) |
|---|---|---|
| **Mechanical** | Parts, assembly and packaging, sourced calculations, simulation, clearance and motion sweeps, Fabrication Pack; reports mass, volume, center of gravity | CAD, FEA/CFD, code execution |
| **Electrical** | Component selection with you, datasheet intake, compatibility, schematic/PCB, drivers and device-level packets, bring-up coaching | EDA, circuit simulation, firmware build/flash, serial monitor |
| **Software** | High-level logic on any device, autonomy and controls, hub/cloud/AI services, hub-to-device protocol, setup guides | Git, web, package managers, cloud CLIs, MATLAB or equivalent |
| **Procurement** | BOM, cost estimates, invoice import, order and fabrication status, what has arrived | Ledger, spreadsheets, supplier lookups |

**The Electrical/Software boundary is by layer, not by device,** because high-level logic can run on a microcontroller. Ownership is assigned per module:

| Owner | Example: a drone with autonomy on an ESP32 |
|---|---|
| Electrical | `mavlink_task`, `tof_task`, `wifi_task`, camera capture, packet definitions |
| Software | `vfh`, `nav_task`, `mission_task`, everything in `laptop/` |
| Software, using Mechanical's data | MATLAB trajectories and controllers, using mass and inertia from the ledger |

## 5. The Project Ledger

Records are versioned, have an owner department, and link to each other.

| Record | Key fields |
|---|---|
| **Requirement** | Statement, priority, verification method, linked tests |
| **Source** | Type and credibility tier, DOI/URL, stored file, retrieved date, origin (found by an agent or uploaded by you). Uploaded sources go through the same quote checks. |
| **Claim** | Statement, source, page/locator, verbatim quote, applicability conditions |
| **Decision** | Options considered, choice, rationale, approver |
| **Component** | Part number, datasheet, status (proposed, confirmed, ordered, received) |
| **Parameter** | Component, name, min/typ/max, units, conditions, page, confirmed by you (yes/no) |
| **Budget** | Mass, power, cost, thermal; allocations and live roll-up with margin |
| **Interface (ICD)** | Mechanical, electrical or data boundary between two departments |
| **Part** | Part number, revision, material, process, quantity, current or superseded |
| **Task** | Department, assignee, dependencies, state, blocked-by (including parts and datasheets) |
| **Artifact** | CAD file, drawing, firmware, with the versions it was derived from |
| **Order** | Items, supplier, cost, invoice, status, expected date |
| **Debug Session** | See section 7 |
| **Connector** | See section 8 |
| **Change Request** | Proposed change, computed impact, checks reopened, approval |
| **Deviation** | What is disabled or not met, why, affected requirement, what restores it |
| **Release** | Frozen set of parts and files, recipient, date sent |
| **Handoff** | See section 9 |

**Storage works for several people without a server.** The ledger is an append-only event log stored as text in the project's Git repository, with SQLite as a local cache rebuilt from it. Project instructions, READMEs and handoffs are generated from it, so they can't drift or be lost separately.

**Data interfaces are machine-readable.** Electrical publishes a definition, and C headers plus Python/TypeScript parsers are generated from it. For example, a drone telemetry packet:

```yaml
packet: TELEMETRY
id: 0x01
transport: {type: udp, port: 5005, direction: device_to_hub}
rate_hz: 10
endianness: little
size_bytes: 55          # checked at generation time
fields:
  - {name: pkt_type,    type: uint8, const: 0x01}
  - {name: drone_id,    type: uint8}
  - {name: ned_x,       type: float32, unit: m}
  - {name: ned_y,       type: float32, unit: m}
  - {name: heading_rad, type: float32, unit: rad}
  - {name: nav_state,   type: enum8, enum: nav_state_t}
  - {name: tag_id,      type: int8, note: "-1 if no tag visible"}
  - {name: tag_dist_m,  type: float32, unit: m}
  - {name: vfh_blocked, type: "bytes[32]"}
  - {name: is_stuck,    type: uint8}
  - {name: reloc_age_s, type: uint16, unit: s, sentinel: 0xFFFF}
```

The format must also cover variable-length commands and chunked streams such as camera preview. Where a standard exists (MAVLink, Zigbee clusters), the interface references it. Interfaces are versioned, and a breaking change must be acknowledged by every consumer.

**Briefings** are generated for each task: relevant requirements, constraints, interfaces and sources selected by a ledger query, plus a short note from the Controller. An agent that needs more calls `request_context`, which is logged.

## 6. How hallucination is actually controlled

| Layer | Mechanism | Catches |
|---|---|---|
| 1. Sourced claims | A claim must have a verbatim quote and locator; code confirms the quote exists on that page of the stored document. The app extracts PDF text with page numbers itself, so this works with any provider. | Invented papers and findings |
| 2. Datasheet parameters | Each datasheet is uploaded against one specific component, and the app checks that the part number in the PDF matches that component before using it. Tables are read from the PDF page itself, because extracted text scrambled columns and lost minus signs in testing. Extracted with page references; absolute-maximum and safety-related values need your confirmation against a snippet of the page. | Misread or assumed specs |
| 3. Calculations in code | All maths runs in code with unit tracking; each formula links to a Claim. | Arithmetic and unit errors |
| 4. Engineering tools | ERC, DRC, compile, SPICE, FEA with mesh convergence, unit tests, plus CAD audits: export matches model (volume, bounding box, hole sizes), clearance scan, motion sweep | Designs that don't work |
| 5. Compatibility engine | Deterministic rules over Parameters and the connection graph | Incompatible parts |
| 6. Independent Verifier | Fresh-context review before any gate or spending decision | Drift from requirements |

**Compatibility rules for the first version:** supply within device range; logic-level match; source current against total load with margin; GPIO limits; regulator dropout and heat; battery C-rate against peak draw; motor stall current against driver rating; shared operating temperature; I2C address collisions, including across multiplexers; MCU pin conflicts; required pull-ups and decoupling; common ground. Derating margins are a project setting.

Unknown is a valid answer. If a value isn't in the ledger, the agent says so and creates a request (for example, "upload this datasheet"), and the task shows as blocked.

**Changes reopen the checks they affect.** Passing one check never closes the others.

| Change | Checks reopened |
|---|---|
| Material | Strength, stiffness, mass, cost, manufacturing process, thermal |
| Geometry | Clearance, motion sweep, strength, mass, drawings |
| Component | Compatibility, power, mass, firmware driver, packaging, cost |
| Interface | Generated code and every consumer's tests |
| Feature disabled | Linked requirements, safety checks, documentation, via a Deviation |

## 7. Guided bring-up: no more "paste error, press enter"

Every firmware or hardware problem opens a **Debug Session** that records the symptom, an error fingerprint, hypotheses, measurements and outcomes. Code problems live in that device's Software workspace, and Electrical's **Troubleshooting** panel covers wiring, connections and possible device faults. If fixing the code doesn't work, the problem is elevated to a **possible device issue** and moves to Troubleshooting with everything already tried. This happens automatically at the fourth repeat in code, or earlier if you choose. The agent then checks power, connections, and whether the part itself is faulty, including swapping in a known-good one. If the device checks out, the session returns to code and keeps its history.

Before proposing a change, the agent must state four things:

1. The hypothesis.
2. The primary source (datasheet page, reference manual, SDK docs) and a **backup source** (application note, vendor example, known-good open-source code).
3. What you should observe if it is right.
4. What result would rule it out.

| Repeats of the same error | Behaviour |
|---|---|
| 1st | Normal hypothesis and fix |
| 2nd | No more code changes until a measurement is taken (multimeter, logic analyzer, serial log, register dump) |
| 3rd | Stop patching. Build a minimal reproduction, re-read the source, and bring in the Research Engineer with fresh context |
| 4th | Summarize what has been ruled out, and give you a manual procedure or a precise question to ask the vendor or community |

**Bring-up procedures are generated from the ledger and record their results:** tests staged from safest to riskiest, exact commands, the log lines to expect, a lookup from each failure message to its likely cause, and a typed confirmation before anything can move or fly. Each run is logged against the requirement it verifies, with the firmware and hardware versions used.

**How debugging starts.** The app starts an investigation when it detects a failure (a failed build or test, lost telemetry, an upload timeout), or when you report a problem. Starting an investigation grants no permission to change anything.

**Who does what:**

| Who | What |
|---|---|
| Agent, automatically | Anything that only reads or is safely contained, when that is the efficient way: reading short logs, builds, tests, simulators, packet and datasheet checks |
| Agent, after your approval | Accepting a code fix, changing a setting, flashing firmware |
| You, when it's physical or out of reach | Measurements, reseating a wire, power cycling, running something on an unconnected machine |
| You, when it's more convenient | Anything faster or cheaper for a person to run and watch |
| You only | Arming, running motors, flight |

**Convenience is a deliberate reason to ask you.** The agent asks you to run and observe a test even when it technically could do it, if that would be better or faster. Typical cases are a long-running program where the agent would have to wait on a timeout, a result you can see at a glance, or output long enough to use a lot of tokens. The request states what to run, what to look for, why you're being asked, and how to reply, usually with a one-tap result or a line of text.

When the agent does read output, code first filters it to the relevant lines. Each automatic investigation has a spending cap, runs free code-based checks first, and stops when the escalation ladder says to.

**Link issues cover problems between devices.** They live on the Interface screen, with a notice on each affected device's Debug tab and a count on Project Network. Each one has five checks in order: the sender sent it, it reached the device, the device could read it, the device accepted it, and the device carried it out. Every check needs evidence, and the failed check decides who gets the fix:

| Cause | Goes to |
|---|---|
| Wrong address or port | The workspace of the device with the wrong setting |
| Firewall or network settings | The affected computer's setup |
| Weak signal | Stays in the Link issue until measured |
| Physical fault (radio, antenna, connector, unstable power) | Electrical Troubleshooting |
| Antenna placement | Mechanical |
| Format or version mismatch | Interface |
| Rejected or not executed by the device | That device's workspace |

The issue stays open on the Interface screen until a full end-to-end transfer succeeds.

**Safety requirements for sending commands or trajectories:** partial data never runs, the device confirms complete receipt before starting, each transfer has an ID so it can't run twice or mix with an older one, link loss triggers a defined action such as hold or land, and failed uploads are never retried automatically in flight.

When the agent can't perform a step, it switches to a step-by-step guide. Each step has an expected result you confirm.

## 8. Connectors: you choose and configure the tools

The app ships with **no fixed CAD, EDA or cloud platform**. It defines what a department needs, then guides you to connect whatever you have.

**Capabilities are the contract.** Agents ask for a capability, and the connector supplies it.

| Department | Example capabilities |
|---|---|
| Mechanical | `cad.run_script`, `cad.export_step`, `cad.mass_properties`, `cad.clearance`, `cad.make_drawing`, `fea.solve` |
| Electrical | `eda.schematic`, `eda.erc`, `eda.drc`, `spice.run`, `fw.build`, `fw.flash`, `serial.monitor` |
| Software | `vcs.*`, `pkg.install`, `cloud.deploy`, `sim.run` |
| Research | `lit.search`, `lit.fetch` |

**The Setup Guide** is an agent that runs when a project starts or a capability is missing:

1. **Find out what you have.** Asks what you own, and with your permission checks what is installed.
2. **Suggest options** from an editable catalog, with trade-offs: official or community, last updated, what it covers.
3. **Walk you through installation** one step at a time, each with an expected result. You run anything needing your account or machine. Failures open a Debug Session, so section 7 applies here too.
4. **Save the configuration per project**, with secrets kept in the OS keychain and referenced by name.
5. **Run a health check** that proves each capability with a harmless test (make a cube, read its mass, export STEP, compare).
6. **Record it in the ledger:** version, source, what passed.

**You can add any MCP server** by pasting its command or URL. The app lists its tools, proposes which capability each provides and a risk class (read, write, hardware, spending), and you confirm. If a server's tools or descriptions change later, it needs re-approval, because a changed description is a way to slip in malicious instructions.

**Every capability has a fallback ladder,** so a missing server never stops the work:

```
MCP server -> vendor scripting API -> command-line tool -> file exchange -> guided manual steps
```

A missing capability is shown with its consequence, for example: "No clearance check connected: you must attest to this at the design review."

**CAD edits are transactions,** whichever tool is connected: checkpoint the model, apply one named change, run the affected checks, then keep it or roll back. Each kept change records its intent and result, and experiments stay in a disposable sandbox.

**Only tools mapped to a capability are shown to an agent.** One FreeCAD server lists over 150 tools, and loading them all on each turn would burn the budget.

**Autonomy is set per capability by you:** Observe, Suggest, Act with approval, Act. Everything starts at Suggest. Flashing, moving hardware, cloud deployment and spending can never go above Act with approval.

### AI model providers

AI access is a connector too. You add an API key, and any supported provider can run the agents. **OpenAI is the first provider built,** using your credits. **Anthropic is second,** followed by a generic adapter for OpenAI-compatible hosts such as OpenRouter or a local model. Each project chooses its own provider.

**Agents ask for a tier, not a model:**

| Tier | Used for | OpenAI default (November 2026) | Input / cached / output per 1M |
|---|---|---|---|
| Fast | Datasheet first pass, invoices, log summaries, screening | `gpt-6-luna` | $0.10 / $0.01 / $0.50 |
| Standard | Concept discussion, Controller, department work | `gpt-6.1-terra` | $1.00 / $0.05 / $5.00 |
| Deep | Research synthesis, Verifier | `gpt-6.1-sol` | $2.00 / $0.10 / $10.00 |

`gpt-6-astra` ($10 / $1 / $50) is available as an override, but it is not a default under a $20 limit. You can reassign any tier or any single agent.

**The Setup Guide also handles providers:** choose a provider or enter a custom OpenAI-compatible URL, store the key in the OS keychain, list the available models, and run a health check. The check confirms what each model supports (tool calling, structured output, images or PDFs, reasoning control, caching, batch) and records it. If a model lacks something a tier needs, the app says so and won't assign it.

**Rules that keep the app portable:**

- **The app owns the agent loop, tool calls and MCP connections,** so connectors work the same with every provider. Provider-hosted tools and conversation storage are off by default.
- **A provider switch loses nothing,** because project truth is in the ledger and briefings rebuild the context.
- **Each provider gets its own adapter.** OpenAI's newer models require the Responses API for full tool calling, so a generic Chat Completions path isn't enough. Other OpenAI-compatible hosts use a separate adapter.
- **Models must qualify before safety-relevant work.** Failure replays (section 13) run against a provider and model pair. A model that hasn't passed is labelled unqualified and can't serve as Verifier without your explicit override.
- **The spend limit needs prices.** A versioned, editable price table turns the usage reported by each provider into dollars. If a model's price is unknown, you enter it before the first run. Estimated usage is labelled as estimated.
- **Project data goes to the provider you choose.** The Setup Guide states this, and a local model can be used for sensitive projects if it qualifies.

### Starting catalog

Suggestions the Setup Guide can offer, not dependencies. Status is from November 2026, mostly from third-party listings.

| Tool | Route | Status |
|---|---|---|
| Fusion | Python scripting API; Autodesk MCP servers | Official servers reported April 2026, unconfirmed |
| SolidWorks | Community MCP server, or its COM API | No official server; may break across versions; needs a test |
| FreeCAD | neka-nat or spkane MCP servers, CalculiX for FEA | Community |
| KiCad | `mcp-server-kicad`, `kicad-mcp-pro`, `kicad-cli` | Several active projects; KiCad version matters |
| Firmware | ESP-IDF, PlatformIO, Arduino CLI | Mature CLIs |
| MATLAB | `matlab -batch`, or an MCP server if one suits | Needs a test |
| Circuit simulation | ngspice | Mature |
| Literature | Semantic Scholar, arXiv, OpenAlex | OpenAlex has required a free key since February 2026. For paywalled papers, you upload the PDF. |

### Safety and security

- Papers, web pages, datasheets, repositories and MCP tool descriptions are untrusted. Agents reading them don't get flash, purchase or deploy permissions in the same step.
- The platform never places orders itself.
- Gate approvals, autonomy changes and budget changes can only be made by a person in the UI. No agent credential can write them.
- Lithium batteries, mains voltage, moving machinery and flight get a safety checklist at the design review, and the UI states that outputs need qualified human review. Drones also get a check of local regulations.

## 9. Working with other people

The goal is that people and agents coordinate through shared project state, so nobody is hand-carrying status files between sessions.

- **Each person owns departments or modules,** and their agents work within those.
- **All shared state lives in the project repository,** including agent instructions. The app warns about uncommitted or unsynced project state.
- **Asks are tracked records,** with an owner and a status, so they can't get lost in a document.
- **Contracts and stubs come first,** so others can start in parallel.
- **Handoffs are a generated view of the ledger,** for someone joining or working outside the app. They are always current when generated and contain what exists, ordered next steps with checks, open asks, and known deviations.
- **The app writes `AGENTS.md` and `CLAUDE.md` from the ledger,** so anyone can continue in Claude Code or Codex directly with the same context.

Passwords, keys and account IDs never appear in generated files.

## 10. Working within $20 per project

Each project has its own AI spending limit, $20 by default and editable. It covers the whole project and does not reset monthly. This is separate from the project's hardware budget, and from any cost of developing this app. The limit is why agents use the model tiers in section 8.

**What $20 buys, estimated at OpenAI's prices on 2026-11-08.** Assumes a turn with 20k input tokens, 15k of them cached, and 1.5k output. Reasoning tokens and cache-write charges can push this higher, so real figures come from Phase 0.

| Tier and model | Approx. cost per turn | Turns for $20 | 15-turn tasks for $20 |
|---|---|---|---|
| Fast, `gpt-6-luna` | $0.0014 | 14,000 | 950 |
| Standard, `gpt-6.1-terra` | $0.013 | 1,500 | 100 |
| Deep, `gpt-6.1-sol` | $0.027 | 750 | 50 |
| Override, `gpt-6-astra` | $0.14 | 140 | 9 |

That suits a small project. A project the size of the stair climber or drone could still exceed it, so the app shows the forecast at G2 and you decide whether to reduce scope, use cheaper models, or raise the limit. Disciplined work helps here, since trial-and-error loops are the most expensive way to use a model.

**Default split per project, editable:**

| Area | Per project |
|---|---|
| Headquarters, including research and Verifier | $6.00 |
| Electrical | $5.00 |
| Mechanical | $4.00 |
| Software | $3.00 |
| Procurement | $0.50 |
| Reserve | $1.50 |

**Rules that keep it inside the limit:**

- Warn at 50% and 80%. At 100%, new agent runs stop until you raise the limit. The ledger, compatibility engine, and connector checks cost nothing and keep working.
- Show a cost preview before anything expensive, such as a full research pass or reading a long manual.
- Read each datasheet once on the Fast tier, using batch pricing where the provider offers it and waiting is acceptable, then load only the relevant pages.
- Run code checks first and call a model only when they fail or need explaining.
- Use the Verifier at gates only, on a diff where possible.
- Develop against recorded tool calls and model responses. Full live test runs happen only before milestones.

## 11. Project lifecycle and architecture

Each gate requires your approval.

```
G0 Intake       scope, aim, budget, skill level, connect your tools
   |
G1 Concepts     research -> cited options -> high-level discussion -> you pick
   |
G2 Baseline     requirements + budgets (mass, power, cost, thermal) + task graph + timeline
   |
   +--> Mechanical ----+
   +--> Electrical ----+--> G3 Interface freeze --> Software (real devices)
   +--> Procurement ---+
   |
G4 Design review   against requirements, before spending money
   |
G5 Build and bring-up   orders placed, parts arrive, tasks unblock
   |
G6 Integration and test   every requirement traced to a test result
```

### Importing an existing project

A project can also start from existing work: a folder, Git repository or zip file. The original files are only ever read, never moved or changed. Copying a whole project isn't needed, and would force cloud-only files to download.

| Step | What happens |
|---|---|
| 1. Local scan | Lists code, CAD, drawings, datasheets, invoices and Git history. It uses no AI, costs nothing and sends nothing off your computer. |
| 2. Cost and privacy preview | Shows what would go to the AI provider, what is redacted and the estimated cost. You approve it first. Only files that matter are read, to protect the spending limit. |
| 3. Draft project record | Proposes requirements, parts, components, devices, sub-circuits, interfaces and orders. Each is marked **Imported, unverified** and links to the file it came from. |
| 4. Your confirmation | Nothing becomes project truth until you confirm it. Device tabs and sub-circuits are suggestions too. |
| 5. Gap report | Flags conflicting revisions, obsolete files, missing datasheets, disabled features, drifted documents, untested requirements and incomplete purchase records. |
| 6. Choose where to resume | You pick the current stage. Earlier gates are marked **Imported, not reviewed**, with their gaps listed. |

**Gaps block only the work they affect.** A missing datasheet blocks work on that component, so you aren't forced to document an entire old project before fixing one bug. Safety checks still apply to anything you build, flash or run from then on.

**Existing code stays as ordinary code.** It gets descriptions only when an agent touches it or you ask, like any other code.

### Where the app runs

The app runs locally, because your CAD, flashing and serial ports are on your PC.

```
Web UI (localhost)
       |
Backend: orchestrator, ledger, compatibility engine, citation checker,
         loop detector, budget guard, handoff generator
       |
Agent runtime (per-agent tier, permissions, skills)
       |
       +--> Model gateway (provider adapters, usage, prices) --> OpenAI, then any provider
       |
Connector manager (capabilities, health checks, fallbacks, autonomy levels)
       |
Whatever you connected: MCP servers, scripts, CLIs, files
```

| Part | Choice | Reason |
|---|---|---|
| Requests and changes | A Fast Router agent decides who handles a request, reuses earlier answers, and declines what's out of scope. Headquarters settles routine agent-raised part changes itself. Code forces a person's decision for requirement, component and interface changes, anything the reviewer questions, over-budget changes and parts already released. A person's own requests are never settled for them. | Added on 2026-11-09 at the owner's request, so only necessary decisions reach them. Headquarters' delegated authority covers only parts and follow-up tasks, is labelled in the record, and can never approve gates. |
| How agents are run | Headquarters is the app's own code (`headquarters.py`). It decides the next step from the project's state and stops only where a person must review. People talk to one agent per department. Departments reach each other only through Headquarters, and every request is logged. | The sequence can't be skipped by a model, and spending and approvals stay enforced in one place. OpenAI's hosted Agents API was reviewed on 2026-11-09 and not adopted: it is in public beta, runs OpenAI's own harness, and doesn't document manual approvals or reaching local tools such as Fusion and USB devices. |
| Agent runtime | OpenAI Agents SDK, behind the app's own model gateway interface (replaced Pydantic AI on 2026-11-09) | Runs appear in OpenAI's Traces with their steps. Every agent's prompt, labelled inputs, tools and output are defined in `agent_specs.py` and shown on the Agents screen. A second provider now needs its own adapter behind the gateway, and its runs won't appear in OpenAI's Traces. |
| Dependencies | Exact versions with hashes; LiteLLM not used | LiteLLM's PyPI releases were compromised in March 2026, and a package holding every provider key is a high-value target. |
| Backend | Python, FastAPI | CAD, simulation and scientific libraries are Python-first |
| Ledger | Event log in Git, SQLite cache | No server, no running cost, merges through Git |
| Frontend | React, TypeScript, React Flow, Three.js | React Flow matches your existing network app |

## 12. User interface

The screens come from what the platform needs. For visual style only, I kept what you liked in the 3Birds pages: dark graph canvas, right-hand inspector, legend, top stat strip, monospace labels, and a semantic color for each kind of actor (human, AI, data). Node shape shows actor type and line style shows read or write access.

| Screen | Content |
|---|---|
| **Project Network** (home) | Departments, agents, people and ledger stores as a live graph; stats for gate, budget, spend, mass margin, blocked tasks |
| **Access Map** | What each agent can read and write, so need-to-know is visible |
| **Walk Through a Change** | Replays a Change Request across the graph, showing every check it reopens |
| **Task graph and timeline** | Dependencies, what is blocked and by what |
| **Sources and decisions** | Click a claim to see the highlighted passage |
| **Mechanical** | Chat, 3D viewer, parts and revisions, clearance results, Fabrication Pack |
| **Electrical** | Chat, component table with one datasheet upload per component, circuits, compatibility results, Troubleshooting. Circuits have one tab per sub-circuit, created when needed or when you ask. Sub-circuits can connect to each other through labelled ports, or stand alone. |
| **Software** | An IDE with one tab per device. Each tab is that device's workspace: files, agent, code debugging and setup. Agent-touched code appears as description panels (below). |
| **Interface** | Its own screen, shared by departments: data packets with byte layouts and who must acknowledge changes, electrical and mechanical interfaces, and Link issues. |
| **Procurement** | BOM, invoices, orders, cost against budget |
| **Connectors** | Capabilities, health, fallbacks, autonomy levels, Setup Guide |
| **Review queue** | Everything waiting on you, in one list |

**Agent-touched code is documented, and the app enforces it.** Descriptions are generated only where needed: for any file an agent has written or edited, or that you ask to have described. Code you wrote stays as ordinary code unless you ask. A description states what the code does, what it takes and gives, and what it is based on (requirement, datasheet page, source). Each described block sits behind a panel you open to see the code, and a "Full file" view is always available. A small or simple file can use one description covering the whole file.

- **Files stay normal source code** that compiles and works in Git and other editors. Descriptions are stored as structured comments above each block.
- **An agent can't complete a change that leaves its own code undescribed.** This is a code check, so it doesn't depend on the agent choosing to comply.
- **Descriptions can't silently go stale.** Each one records a hash of its code. If the code changes and the description doesn't, the panel is flagged "Description out of date" and the task can't close.
- **The same workspace holds firmware,** with each file showing whether Electrical or Software owns it. Generated files are read only.

Each gate also exports a **standalone HTML report**, so a teammate or supervisor can open it without the app.

## 13. Build roadmap

### Phase 0: Feasibility tests (2 weeks, about $5 of API spend)

| Test | Pass condition |
|---|---|
| Purchase records | Extract orders from messy invoices, and flag every missing field |
| Interface recovery | Recover drone packets from existing C and Python; generated parsers are byte-compatible |
| Datasheet extraction | On 10 public datasheets, at least 95% of electrical limits correct, with page numbers, on the Fast tier |
| Provider portability (when Anthropic credits are available) | The same tool-using task completes on OpenAI and Anthropic, with correct spend tracking, using the chosen runtime |
| CAD transaction | Checkpoint, one edit, audit and rollback through a connected CAD tool, on a copy of the model |
| CAD generation | New bracket and enclosure from a spec, with correct dimensions |
| Handoff cold start | A fresh agent given only a generated handoff can say what exists, what's next, and what's blocked |
| Research quality | For the butterfly drone: all cited papers exist, and quotes match the documents |
| Real cost | Measure actual cost per task and correct section 10 |

If CAD generation fails, Mechanical focuses on audits, calculations and controlled edits to models you create.

**Results so far (2026-11-08, total spend $0.23, code in `phase0/`):**

| Test | Result | Detail |
|---|---|---|
| Runtime on OpenAI | Passed | Pydantic AI, Responses API, a local MCP server, structured output and usage tracking worked on `gpt-6-luna`, `gpt-6.1-terra` and `gpt-6.1-sol`. |
| Interface recovery | Passed | From C only, `gpt-6-luna` and `gpt-6.1-terra` recovered all 6 packet layouts, matching the independent Python definitions. A packet built from the recovered layout parsed correctly with the real parser. |
| Research quality | Passed, limited | On Sol and Luna, all 6 cited claims per model came from real arXiv papers, and code confirmed each quote is verbatim. This proves citations are genuine, not that the engineering conclusions are right. arXiv abstracts only. |
| Provider portability | Deferred | Waits until you have Anthropic credits. Does not block Phase 1, which builds only the OpenAI adapter. Until then, the model gateway is tested with an offline fake model so it can't quietly depend on OpenAI. |
| Purchase records | Passed, limited | On 16 redacted PDF orders, Luna and Terra each got 14 totals right against the team's spreadsheet and correctly refused to state the other 2, with no wrong totals. Pre-tax and tax matched on 13 of 16. Only Terra flagged DigiKey's unpaid import duties, so that flag needs a code rule or a higher tier. Not tested: 6 of the 22 orders (screenshots, a form, and one PDF the redaction check refused). |
| Redaction | Needs hardening | The local check held back one document as designed, but the last four card digits reached OpenAI on 2 documents before a fix was applied. The app needs a redaction preview before sending. |
| Datasheet extraction | Just below target | On 11 public datasheets and 59 hand-checked absolute-maximum values, sending the PDF page scored 94.9% on Luna (target 95%) and 93.2% on Terra, with every miss an omission and no wrong value. Sending extracted text scored only 88.1% and 78.0%, and produced wrong values such as a lost minus sign. **Decision: send PDF pages for tables, and use text only for quote checks.** Absolute-maximum tables only. |
| CAD generation | Passed, simple parts | A bracket, a revision of it and an enclosure passed checks on the exported STEP (size, volume, hole sizes, valid solid) on Luna and Terra. Two of the three parts needed one automatic retry. Hole positions and complex geometry were not tested. Generated with CadQuery, so it doesn't yet show that this works in Fusion or SolidWorks. |
| CAD transaction, handoff cold start, real cost | Not run | CAD transaction needs a connected CAD tool, the handoff test needs the ledger, and real cost needs a full-length task. |

**Interface prototype:** a clickable front end with sample data is in `app/`. No agents or ledger are connected.

### Phase 1: Spine, Headquarters, Connectors (5 weeks)

**Status (2026-11-09): exit condition met.** Built in `backend/` and the Headquarters and Connectors screens: event-log ledger with rebuildable cache, gates with coded prerequisites, person-only approvals, per-project spend limit, tier-based model gateway with the OpenAI adapter, all four HQ agents with quote checking, keychain key storage, Connector Manager, Setup Guide, a Git repository per project, and generated handoffs. 74 offline tests pass. In a run with real OpenAI models and arXiv, tools were connected through the guide, then a project went from idea to approved Gate 2 for $0.094.

**Connections belong to the computer, not the project,** because installed tools and keys differ per machine.

**Carried into later phases:** department agents using connected tools (Phase 2 onward), connecting the sample department screens to the ledger, opening a Debug Session when setup fails, and login for the local server before anyone else can reach it.

Ledger, gates, UI shell, spend guard, model gateway with the OpenAI adapter, four HQ agents, citation checking, task graph, Connector Manager and Setup Guide.
**Done when:** you connect your tools by following the guide, then take an idea to an approved, cited concept for under $3.

### Phase 2: Electrical (6 weeks)

**Status (2026-11-09): exit condition met in design and compile, not yet on hardware.** Built: one datasheet per component with a part-number check, limits read from PDF pages with page links, a 12-rule compatibility engine, power tree, connection proposals, firmware that compiles through PlatformIO and carries enforced descriptions, Debug Sessions with the ladder enforced in code, and automatic MCP re-checks before agent sessions with a before-and-after view. 120 offline tests pass. In a real-model run, four public datasheets produced 146 limits, a seeded fault was caught with its datasheet page, and the generated TMP102 driver compiled, for about $0.06. Since then: agents search datasheets and open pages themselves, a page memory links exact quotes to the code that uses them, and the Software workspace shows real firmware and debug sessions. **Deferred or not built:** flashing and generated bring-up procedures (deferred at your request), circuit diagrams drawn from real connections, and testing on physical hardware.

Datasheet inbox, Parameters, compatibility engine, power tree, firmware connectors, Debug Sessions, bring-up procedures.
**Done when:** a small ESP32 project is built from uploaded datasheets, and a deliberately incompatible part is caught.

### Phase 3: Mechanical checks, Procurement and project import (5 weeks)

**Fusion (2026-11-09):** connected through Autodesk's built-in MCP server, using whichever port Fusion shows. Agents get narrow tools rather than Fusion's raw ones, because one of those also opens the Purchase Manager and can save or close documents. CAD transactions and a Mechanical agent work on a blank design with real models: checkpoint, approved change, verify, keep or undo, and automatic recovery from a failed script. Also working in real Fusion: API lookup from the installed classes, interference that separates overlap from touching, intended contacts, part tags on bodies, kept changes proposing part revisions, and joint sweeps (which require a grounded component). Tested on a saved copy of a real 1,589-body assembly: read-only inspection, a checkpointed edit with undo, bounding-box clearance within a time budget, and a generated two-sheet PDF drawing. Fusion's drawing API is a preview feature and its automatic dimensions are limited, so drawings need review. Motion sweeps were tested only on a simple assembly, because the copy has no joints.

**Status (2026-11-09): main records and checks working.** The list below is from before the Fusion work. Built: parts and revisions, change-driven reopened checks with evidence, mass and cost roll-up, free STEP measurement and clearance, frozen fabrication packs, order logging with flags, invoice reading with a redaction preview, and the import flow with resume points. 157 offline tests pass. Real-model run on synthetic parts, invoice and notes. The free import checks also ran read-only on the three past projects and found P24's two sizes, the disabled ToF sensor, and the missing Master Spec. **Not built yet:** a Mechanical agent, CAD transactions through a connected CAD tool, motion sweeps, drawings, and orders unblocking tasks. The firmware-exists gap check also gave a false positive on 3Birds, a software project.

Part records and revisions, Releases, audits, clearance and sweeps, mass and cost roll-up, generated Fabrication Pack, BOM, order logging, and the import flow.
**Done when:** a material change reopens strength and mass checks, an exported pack contains no superseded parts, and a copy of the stair climber imports with its gaps reported correctly.

### Phase 4: Interfaces, Software, Collaboration (4 weeks)

Packet generation, simulators, Software agent, setup guides, handoffs, Git sync between people.
**Done when:** two ESP32 nodes talk to a hub using generated code, and a second person continues from a handoff alone.

**Status (2026-11-09):** built, and met except on real hardware. C, Python and TypeScript are generated from one definition and agree byte for byte. Two compiled C nodes exchanged 40 packets and 40 acknowledgements over local UDP with a hub written by the Software agent. A second person joined through Git, and a fresh agent given only the handoff correctly said what exists, what's next and what's blocked. On 2026-11-09 an ESP32-S2 and an ESP32-C6 exchanged generated packets with a computer hub over USB serial, including refusing damaged replies, flashed by hand. Flashing is now in the app for people only: it checks the chip, builds, backs up, needs approval each time, and listens to the board afterwards. Verified on the real ESP32-S2 and ESP32-C6, including restore and wrong-chip refusal. An S2 and a C6 also exchanged generated packets by radio over ESP-NOW, and the Link Investigator named the cause of six injected faults, after its cause list was corrected. Capture evidence fills the five checks from real logs, for boards over serial and computer programs through an evidence log. A real C6 linked to a computer program over Bluetooth Low Energy passed a healthy-link check and four injected faults using only captured evidence. **Still open:** a test where the radio itself is at fault, a link through a Wi-Fi router, and flashing boards other than ESP32 on real hardware. Link issues record people's evidence and simulator results. Agents can only suggest tests. Only Python syntax is checked for computer software, and agent-written programs are never started automatically.

### Phase 5: Design generation and integration (5+ weeks, scope set by Phase 0)

CAD generation if viable, PCB flow with STEP handoff, Change Requests with impact analysis, requirement-to-test tracing, gate reports.

**Status (2026-11-09):** Change Requests, requirement-to-test tracing with a dependency graph, gate reports and Gates 3 to 6 are built. Still to do: CAD generation and the PCB flow. Dropping a requirement, and a formal Deviation record, aren't built yet.
**Done when:** a component swap correctly flags every affected calculation, drawing and task.

**Total: about 6 months.** Phases 0–2 (about 3 months) already give you a useful Electrical assistant.

### Failure replays

The test is whether the platform would have **prevented or caught** what went wrong, not whether it reproduces those projects.

| Replay | Must happen |
|---|---|
| **Late cost** (stair climber) | An over-budget material choice is flagged at design time |
| **Aluminium to acrylic** (stair climber) | Strength, mass and cost checks reopen |
| **Changed after sending** (stair climber) | A change to released parts shows what was already sent |
| **Obsolete P24 files** (stair climber) | Superseded parts never appear in an export |
| **ToF switched off** (drone) | A Deviation is required, and the docs reflect it |
| **Packet changed on one side** (drone) | The mismatch is caught at build time |
| **Docs deleted** (3Birds) | Knowledge survives and broken references are flagged |
| **Trust range 0–5 vs 0–3** (3Birds) | The change needs the consumer's acknowledgement |
| **Seeded faults** | Fake citations, wrong voltages, unit errors, pin conflicts: measured catch rate |
| **Debug loops** | Scripted repeat errors: does escalation fire? |
| **Import** (stair climber) | The original folder is unchanged, everything imported is unverified until confirmed, and known problems appear in the gap report |

Run these on copies, never on your original folders. Zigbee has no replay yet, so it needs a new build to test.

## 14. Main risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| $20 runs out mid-project | High | Forecast at G2, spend guard, previews, cheaper default models, code checks first |
| Cheaper models miss engineering errors | Medium | Deterministic checks, Deep-tier Verifier at gates, measured catch rate |
| Providers behave differently, or prices and models change | High | Per-provider adapters, health checks, qualification runs, editable price table |
| Agent-made CAD is poor for complex geometry | High | Phase 0 test; audits and edits are useful even if generation isn't |
| User-configured connectors vary widely | High | Health checks, capability contract, fallback ladder |
| A malicious or changed MCP server | Medium | Risk classes, re-approval on change, no secrets in prompts |
| PDF datasheet tables are misread | Medium | Page-linked values, your confirmation for critical limits |
| Import draws wrong conclusions from messy files, or costs too much | High | Free local scan first, cost preview, unverified status until you confirm, source file link on every item |
| Too many approvals and you stop reading them | Medium | Approvals only at gates and safety actions; concise diffs |
| Scope grows before the core works | High | Phase exit criteria; one complete vertical at a time |
| Unsafe design is built on trust | Low, severe | Safety checklist, Verifier, clear limits on what outputs mean |

## 15. Decisions for you

| Question | Default |
|---|---|
| Personal tool or a product for others? | Personal and small teams first |

## 16. First steps

1. Create an OpenAI API key for development, using your credits.
2. Confirm the questions in section 15.
3. Run the Phase 0 tests and update this plan with the results.
4. Write the Ledger event schema and start Phase 1.

## Sources for tool support claims

Mostly third-party roundups and package listings, so the Setup Guide must recheck them.

- [CAD MCP server comparison](https://www.getleo.ai/blog/cad-mcp-server-comparison-2026)
- [CAD and 3D modeling MCP servers review](https://chatforest.com/reviews/cad-3d-modeling-mcp-servers/)
- [FreeCAD MCP servers comparison](https://spkane.github.io/freecad-addon-robust-mcp-server/latest/COMPARISON)
- [mcp-server-kicad](https://pypi.org/project/mcp-server-kicad/)
- [kicad-mcp-pro](https://github.com/oaslananka/kicad-mcp-pro)
- [OpenAlex authentication](https://developers.openalex.org/api-reference/authentication)
- [Semantic Scholar API](https://semanticscholar.org/product/api)
- [FreeCAD FEM CalculiX](https://wiki.freecad.org/FEM_CalculiX)
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) and [models](https://developers.openai.com/api/docs/models)
- [OpenAI prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) and [file inputs](https://developers.openai.com/api/docs/guides/file-inputs)
- [Pydantic AI models and providers](https://pydantic.dev/docs/ai/models/overview/) and [MCP client](https://pydantic.dev/docs/ai/mcp/client/)
- [LiteLLM supply chain compromise](https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/)
