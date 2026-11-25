# Proposed with exe="matlab" (or "octave" with --eval): runs one analysis script the project already has and keeps
# the figures it makes. The script itself is the project's; this only runs it in batch mode and collects the output.


def collect(project, out, tool):
    scripts = sorted(project.glob("analysis/*.m"))
    if not scripts:
        raise SystemExit("No MATLAB script in this project's analysis folder (analysis/*.m).")
    script = scripts[0]
    out_m = str(out).replace("\\", "/")
    # Every open figure is saved as a PNG into out after the script runs.
    batch = (f"cd('{str(script.parent).replace(chr(92), '/')}'); run('{script.name}'); "
             f"figs = findall(groot, 'Type', 'figure'); for k = 1:numel(figs), exportgraphics(figs(k), sprintf('{out_m}/figure_%d.png', k)); end")
    ran = tool.run(["-batch", batch], timeout=170)
    if ran["code"] != 0:
        raise SystemExit("MATLAB stopped: " + (ran["stderr"] or ran["stdout"]).strip()[-400:])
    figures = sorted(p.name for p in out.glob("figure_*.png"))
    return {"script": script.relative_to(project).as_posix(), "printed": ran["stdout"][-4000:], "figures": figures}
