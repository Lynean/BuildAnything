"""Forward kinematics and self-collision for a robot imported from CAD. Pure Python, no imports beyond math.

This file is also copied, unchanged, into each computer program's generated/robot_model.py with the
project's model appended, so the laptop app checks collisions with exactly the code the backend tested.

The model is a dict:
  root: the base link's name
  joints: [{name, type (revolute, prismatic or fixed), parent, child, xyz, rpy, axis, lower, upper}] in URDF order
  links: {name: {"boxes": [{"c": [x, y, z], "h": [hx, hy, hz]}]}}  boxes are axis-aligned in the link's frame,
         fitted around each CAD part, so they are slightly larger than the real parts (never smaller)
  ignore: [[link_a, link_b]]  pairs never checked: joined by a joint, or already touching as assembled
Angles are radians and lengths metres.
"""
import math


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _apply(r, v):
    return [r[0][0] * v[0] + r[0][1] * v[1] + r[0][2] * v[2], r[1][0] * v[0] + r[1][1] * v[1] + r[1][2] * v[2], r[2][0] * v[0] + r[2][1] * v[1] + r[2][2] * v[2]]


def rpy_matrix(roll, pitch, yaw):
    """URDF's fixed-axis roll, pitch, yaw: R = Rz(yaw) Ry(pitch) Rx(roll)."""
    cr, sr, cp, sp, cy, sy = math.cos(roll), math.sin(roll), math.cos(pitch), math.sin(pitch), math.cos(yaw), math.sin(yaw)
    return [[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
            [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
            [-sp, cp * sr, cp * cr]]


def axis_matrix(axis, angle):
    """Rotation by angle about a unit axis (Rodrigues)."""
    n = math.sqrt(sum(a * a for a in axis)) or 1.0
    x, y, z = (a / n for a in axis)
    c, s, t = math.cos(angle), math.sin(angle), 1 - math.cos(angle)
    return [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
            [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
            [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]


def movable(model):
    """The joints that move, in the order joint angles are given."""
    return [j for j in model["joints"] if j["type"] in ("revolute", "prismatic", "continuous")]


def limits(model):
    return [(j["lower"], j["upper"]) for j in movable(model)]


def _angles(model, q):
    names = [j["name"] for j in movable(model)]
    if isinstance(q, dict):
        return {n: float(q.get(n, 0.0)) for n in names}
    q = list(q)
    if len(q) != len(names):
        raise ValueError(f"expected {len(names)} joint values ({', '.join(names)}), got {len(q)}")
    return dict(zip(names, (float(v) for v in q)))


def forward(model, q):
    """Each link's pose as (rotation, position) in the base frame."""
    values = _angles(model, q)
    poses = {model["root"]: ([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]], [0.0, 0.0, 0.0])}
    pending = list(model["joints"])
    while pending:
        progressed = False
        for j in list(pending):
            if j["parent"] not in poses:
                continue
            pr, pt = poses[j["parent"]]
            r = _matmul(pr, rpy_matrix(*j.get("rpy", (0.0, 0.0, 0.0))))
            t = [pt[i] + _apply(pr, j["xyz"])[i] for i in range(3)]
            v = values.get(j["name"], 0.0)
            if j["type"] in ("revolute", "continuous"):
                r = _matmul(r, axis_matrix(j["axis"], v))
            elif j["type"] == "prismatic":
                n = math.sqrt(sum(a * a for a in j["axis"])) or 1.0
                t = [t[i] + _apply(r, [a / n * v for a in j["axis"]])[i] for i in range(3)]
            poses[j["child"]] = (r, t)
            pending.remove(j)
            progressed = True
        if not progressed:
            raise ValueError("the joints don't form a tree from the root link")
    return poses


def _world_boxes(model, poses):
    out = {}
    for link, info in model["links"].items():
        if link not in poses:
            continue
        r, t = poses[link]
        boxes = []
        for b in info["boxes"]:
            c = _apply(r, b["c"])
            boxes.append(([c[i] + t[i] for i in range(3)], r, b["h"], math.sqrt(sum(h * h for h in b["h"]))))
        out[link] = boxes
    return out


def boxes_overlap(c1, r1, h1, c2, r2, h2, margin=0.0):
    """Separating-axis test for two oriented boxes. r's columns are each box's axes; h are half-sizes."""
    a = [[r1[0][i], r1[1][i], r1[2][i]] for i in range(3)]
    b = [[r2[0][i], r2[1][i], r2[2][i]] for i in range(3)]
    d = [c2[i] - c1[i] for i in range(3)]
    axes = a + b
    for u in a:
        for v in b:
            w = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
            n = math.sqrt(sum(x * x for x in w))
            if n > 1e-9:
                axes.append([x / n for x in w])
    for ax in axes:
        ra = sum(h1[i] * abs(sum(a[i][k] * ax[k] for k in range(3))) for i in range(3))
        rb = sum(h2[i] * abs(sum(b[i][k] * ax[k] for k in range(3))) for i in range(3))
        if abs(sum(d[k] * ax[k] for k in range(3))) > ra + rb + margin:
            return False
    return True


def collisions(model, q, margin=0.0):
    """Pairs of links whose shapes overlap at these joint values, closer than margin metres counting as touching."""
    ignore = {frozenset(p) for p in model.get("ignore", [])}
    world = _world_boxes(model, forward(model, q))
    names = sorted(world)
    hits = []
    for i, la in enumerate(names):
        for lb in names[i + 1:]:
            if frozenset((la, lb)) in ignore:
                continue
            found = False
            for ca, ra, ha, sa in world[la]:
                for cb, rb, hb, sb in world[lb]:
                    if math.dist(ca, cb) > sa + sb + margin:
                        continue  # the bounding spheres don't meet, so the boxes can't
                    if boxes_overlap(ca, ra, ha, cb, rb, hb, margin):
                        found = True
                        break
                if found:
                    break
            if found:
                hits.append((la, lb))
    return hits


def path_collisions(model, start, end, samples=24, margin=0.0):
    """Checks a straight joint-space move. Returns None if clear, or (fraction along the move, colliding pairs)."""
    a, b = list(_angles(model, start).values()), list(_angles(model, end).values())
    for k in range(samples + 1):
        f = k / samples
        hits = collisions(model, [x + (y - x) * f for x, y in zip(a, b)], margin)
        if hits:
            return f, hits
    return None


def add_box(model, link, center, half_size):
    """Adds a shape the CAD doesn't have, such as a tool or pen holder, so collisions include it. Metres, in the link's frame."""
    model["links"].setdefault(link, {"boxes": []})["boxes"].append({"c": [float(v) for v in center], "h": [abs(float(v)) for v in half_size]})
