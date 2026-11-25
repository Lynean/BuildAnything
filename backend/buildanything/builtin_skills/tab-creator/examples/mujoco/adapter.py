import json


def collect(project, out, tool):
    import mujoco

    sim = project / "mechanics" / "sim"
    if not sim.is_dir():
        raise SystemExit("No simulation folder (mechanics/sim) in this project yet.")
    spec = sim / "_results" / "spec.json"
    name = None
    if spec.exists():
        scenarios = json.loads(spec.read_text(encoding="utf-8")).get("scenarios") or []
        name = scenarios[0]["model"] if scenarios else None
    if not name:
        models = sorted(p.name for p in sim.glob("*.xml"))
        if not models:
            raise SystemExit("No MuJoCo model (.xml) in mechanics/sim.")
        name = models[0]
    model = mujoco.MjModel.from_xml_path(str(sim / name))
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)

    frames, every, source = [], 0.02, "settled for 2 s with no motors driven"
    motion = sim / "_results" / "scenario_0.motion.json"
    if motion.exists():
        rec = json.loads(motion.read_text(encoding="utf-8"))
        if rec.get("model") == name and rec.get("qpos") and len(rec["qpos"][0]) == model.nq:
            frames, every, source = rec["qpos"], float(rec["every_s"]), "replayed from the last recorded run"
    if not frames:
        keep_every = max(1, int(every / model.opt.timestep))
        for step in range(int(2.0 / model.opt.timestep)):
            mujoco.mj_step(model, data)
            if step % keep_every == 0:
                frames.append(data.qpos.tolist())
        every = keep_every * model.opt.timestep

    bodies = sorted(range(1, model.nbody), key=lambda b: -model.body_mass[b])[:3]
    names = [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, b) or f"body {b}" for b in bodies]
    times, heights = [], {n: [] for n in names}
    stride = max(1, len(frames) // 300)
    for k in range(0, len(frames), stride):
        data.qpos[:] = frames[k]
        mujoco.mj_forward(model, data)
        times.append(round(k * every, 3))
        for b, n in zip(bodies, names):
            heights[n].append(round(float(data.xpos[b][2]) * 1000, 1))

    pictures, skipped = [], None
    try:
        from PIL import Image

        renderer = mujoco.Renderer(model, 360, 540)
        camera = mujoco.MjvCamera()
        camera.distance = max(0.5, float(model.stat.extent) * 2.0)
        camera.azimuth, camera.elevation = 135, -20
        for i, k in enumerate(sorted({0, len(frames) // 2, len(frames) - 1})):
            data.qpos[:] = frames[k]
            mujoco.mj_forward(model, data)
            camera.lookat[:] = model.stat.center
            renderer.update_scene(data, camera=camera)
            Image.fromarray(renderer.render()).save(out / f"frame_{i}.png")
            pictures.append({"file": f"frame_{i}.png", "at_s": round(k * every, 2)})
    except Exception as exc:  # no OpenGL on this computer: the numbers still count
        skipped = f"{type(exc).__name__}: {exc}"[:200]

    return {
        "model": name, "source": source, "seconds": round(len(frames) * every, 2),
        "facts": {"bodies": model.nbody - 1, "joints": model.njnt, "motors": model.nu, "mass_g": round(float(sum(model.body_mass)) * 1000, 1), "timestep_ms": round(model.opt.timestep * 1000, 2)},
        "series": [{"name": n, "x": times, "y": heights[n]} for n in names],
        "pictures": pictures, "pictures_skipped": skipped,
    }
