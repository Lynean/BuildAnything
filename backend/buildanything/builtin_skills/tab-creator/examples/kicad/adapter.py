import json

# Proposed with exe="kicad-cli": the person approves that exact program. Runs KiCad's own checks and returns them.


def collect(project, out, tool):
    sheets = sorted(p for p in project.rglob("*.kicad_sch") if ".history" not in p.parts)
    boards = sorted(p for p in project.rglob("*.kicad_pcb") if ".history" not in p.parts)
    if not sheets and not boards:
        raise SystemExit("No KiCad schematic (.kicad_sch) or board (.kicad_pcb) in this project.")
    version = tool.run(["version"], timeout=30)["stdout"].strip()
    results = []
    for kind, files in (("erc", sheets[:3]), ("drc", boards[:3])):
        for f in files:
            report = out / f"{f.stem}.{kind}.json"
            cmd = ["sch", "erc"] if kind == "erc" else ["pcb", "drc"]
            ran = tool.run([*cmd, str(f), "--format", "json", "--output", str(report)], timeout=180)
            if not report.exists():
                results.append({"check": kind.upper(), "file": f.relative_to(project).as_posix(), "error": (ran["stderr"] or ran["stdout"]).strip()[-300:] or "kicad-cli wrote no report."})
                continue
            found = json.loads(report.read_text(encoding="utf-8"))
            items = [v for s in found.get("sheets", [{"violations": found.get("violations", [])}]) for v in s.get("violations", [])]
            items += found.get("unconnected_items", [])
            rows = [{"severity": v.get("severity", ""), "rule": v.get("description", v.get("type", "")), "where": "; ".join(i.get("description", "") for i in v.get("items", []))[:200]} for v in items]
            results.append({"check": kind.upper(), "file": f.relative_to(project).as_posix(), "errors": sum(r["severity"] == "error" for r in rows),
                            "warnings": sum(r["severity"] == "warning" for r in rows), "items": rows[:500]})
    return {"kicad": version, "results": results}
