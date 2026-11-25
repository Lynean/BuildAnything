// Static descriptions of the team and how it's connected. Project data comes from the API.

/** The names people see for each gate. The G numbers stay as IDs underneath. */
export const STAGES: Record<string, string> = { G0: "Idea", G1: "Research", G2: "Plan", G3: "Agree", G4: "Check", G5: "Build", G6: "Prove" };

export type Kind = "human" | "ai" | "lead" | "data" | "tool";

export interface Actor {
  id: string;
  label: string;
  initials: string;
  kind: Kind;
  x: number;
  y: number;
  role: string;
  tier?: "Fast" | "Standard" | "Deep";
  reads: string[];
  writes: string[];
  status: string;
}

export const actors: Actor[] = [
  { id: "you", label: "You", initials: "YOU", kind: "human", x: 420, y: 0, role: "Project owner. Only a person can approve gates, autonomy and spending.", reads: ["Everything"], writes: ["Approvals", "Decisions"], status: "2 approvals waiting" },
  { id: "concept", label: "Concept Partner", initials: "CP", kind: "ai", x: 210, y: 120, tier: "Standard", role: "Explores ideas and trade-offs. Nothing it says becomes project truth until sourced and approved.", reads: ["Requirements", "Sources"], writes: ["Speculative drafts"], status: "Idle" },
  { id: "research", label: "Research Engineer", initials: "RE", kind: "ai", x: 630, y: 120, tier: "Deep", role: "Finds papers and open-source work, and extracts claims with verified quotes.", reads: ["Requirements", "Sources"], writes: ["Sources", "Claims"], status: "Verifying 2 claims" },
  { id: "verifier", label: "Verifier", initials: "VR", kind: "ai", x: 630, y: 255, tier: "Deep", role: "Reviews deliverables without seeing the author's reasoning. Runs at gates only.", reads: ["Whole ledger"], writes: ["Review findings"], status: "Waiting for G2" },
  { id: "controller", label: "Project Controller", initials: "PC", kind: "lead", x: 420, y: 175, tier: "Standard", role: "Owns the task graph and budgets, writes briefings, routes updates and detects drift.", reads: ["Whole ledger"], writes: ["Tasks", "Briefings", "Change Requests"], status: "Routing 1 update" },
  { id: "ledger", label: "Project Ledger", initials: "LDG", kind: "data", x: 420, y: 345, role: "The single source of truth: requirements, sources, components, interfaces, decisions, tasks and orders.", reads: [], writes: [], status: "412 records, in Git" },
  { id: "mech", label: "Mechanical", initials: "ME", kind: "ai", x: 80, y: 400, tier: "Standard", role: "Parts, packaging, calculations, simulation and fabrication packs.", reads: ["Mass budget", "Enclosure interface", "Mechanics sources"], writes: ["Parts", "Mass", "Drawings"], status: "CAD transaction open" },
  { id: "elec", label: "Electrical", initials: "EL", kind: "ai", x: 250, y: 520, tier: "Standard", role: "Components, datasheets, compatibility, circuits, drivers and device-level packets.", reads: ["Power budget", "Connector positions"], writes: ["Components", "Parameters", "Packets"], status: "Blocked: 1 datasheet" },
  { id: "soft", label: "Software", initials: "SW", kind: "ai", x: 590, y: 520, tier: "Standard", role: "High-level logic, controls, hub, cloud and AI services. Starts after the interface freeze.", reads: ["Interface contract", "Inertia"], writes: ["Modules", "Tests"], status: "Waiting for G3" },
  { id: "proc", label: "Procurement", initials: "PR", kind: "ai", x: 760, y: 400, tier: "Fast", role: "BOM, orders, landed cost and what has arrived. Never places orders.", reads: ["Components", "Parts"], writes: ["Orders"], status: "2 parts in transit" },
  { id: "cad", label: "CAD", initials: "CAD", kind: "tool", x: 40, y: 575, role: "Your connected CAD tool.", reads: [], writes: [], status: "Healthy" },
  { id: "fw", label: "Firmware", initials: "FW", kind: "tool", x: 250, y: 680, role: "Your build, flash and serial tools.", reads: [], writes: [], status: "Healthy" },
  { id: "git", label: "Git & Cloud", initials: "GIT", kind: "tool", x: 590, y: 680, role: "Repository and cloud connectors.", reads: [], writes: [], status: "Healthy" },
  { id: "supply", label: "Suppliers", initials: "SUP", kind: "tool", x: 800, y: 575, role: "Price and availability lookups.", reads: [], writes: [], status: "Not connected" },
];

export const links: { from: string; to: string; access: "rw" | "r" }[] = [
  { from: "you", to: "concept", access: "rw" },
  { from: "you", to: "controller", access: "rw" },
  { from: "concept", to: "ledger", access: "r" },
  { from: "research", to: "ledger", access: "rw" },
  { from: "verifier", to: "ledger", access: "r" },
  { from: "controller", to: "ledger", access: "rw" },
  { from: "ledger", to: "mech", access: "rw" },
  { from: "ledger", to: "elec", access: "rw" },
  { from: "ledger", to: "soft", access: "rw" },
  { from: "ledger", to: "proc", access: "rw" },
  { from: "mech", to: "cad", access: "rw" },
  { from: "elec", to: "fw", access: "rw" },
  { from: "soft", to: "git", access: "rw" },
  { from: "proc", to: "supply", access: "r" },
];

