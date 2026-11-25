import csv

# Data analysis with no program: reads the project's CSV logs, summarises each numeric column and returns series for
# the tab to plot. Swap in pandas or numpy when the data needs them; keep the output small (sampled to 400 points).


def collect(project, out, tool):
    files = sorted(p for p in project.rglob("*.csv") if "node_modules" not in p.parts and ".pio" not in p.parts)[:5]
    if not files:
        raise SystemExit("No CSV files in this project yet.")
    tables = []
    for f in files:
        with f.open(newline="", encoding="utf-8", errors="replace") as handle:
            rows = list(csv.reader(handle))
        if len(rows) < 2:
            continue
        head, body = rows[0], rows[1:]
        numeric = []
        for i, name in enumerate(head):
            try:
                values = [float(r[i]) for r in body if i < len(r) and r[i].strip()]
            except ValueError:
                continue
            if values:
                numeric.append((name, values))
        if not numeric:
            continue
        x_name, x = numeric[0]
        stride = max(1, len(x) // 400)
        series = [{"name": n, "x": x[::stride][: len(v[::stride])], "y": v[::stride]} for n, v in numeric[1:4]]
        stats = [{"column": n, "min": min(v), "max": max(v), "mean": round(sum(v) / len(v), 4), "count": len(v)} for n, v in numeric]
        tables.append({"file": f.relative_to(project).as_posix(), "x": x_name, "rows": len(body), "series": series, "stats": stats})
    return {"tables": tables}
