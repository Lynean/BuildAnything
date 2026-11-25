import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { Rendered } from "./AgentChat";

// One viewer per kind of file. The file explorer picks by the "viewer" the backend reports for each file.

export function PdfViewer({ url }: { url: string }) {
  return <iframe className="pdf-frame" src={url} title="PDF" />;
}

/** A 3D mesh you can turn with the mouse: drag to rotate, scroll to zoom, right-drag to pan. */
export function ModelViewer({ url }: { url: string }) {
  const box = useRef<HTMLDivElement>(null);
  const [info, setInfo] = useState("Loading the mesh…");
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setClearColor(0x12161d);
    el.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 100000);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x334055, 1.4));
    const sun = new THREE.DirectionalLight(0xffffff, 1.6);
    sun.position.set(1, 2, 3);
    scene.add(sun);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    const size = () => { const w = el.clientWidth, h = el.clientHeight || 500; renderer.setSize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); };
    const watch = new ResizeObserver(size);
    watch.observe(el);
    size();
    let alive = true;
    new STLLoader().load(url, (geometry) => {
      if (!alive) return;
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      const b = geometry.boundingBox!;
      const center = b.getCenter(new THREE.Vector3());
      const dims = b.getSize(new THREE.Vector3());
      geometry.translate(-center.x, -center.y, -center.z);
      const mesh = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: 0xb8c2d0, metalness: 0.1, roughness: 0.6 }));
      scene.add(mesh);
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 25), new THREE.LineBasicMaterial({ color: 0x3d4452 }));
      scene.add(edges);
      const r = Math.max(dims.x, dims.y, dims.z) || 1;
      camera.position.set(r * 1.4, r * 1.1, r * 1.6);
      camera.near = r / 1000; camera.far = r * 100; camera.updateProjectionMatrix();
      controls.target.set(0, 0, 0);
      scene.add(new THREE.GridHelper(r * 3, 30, 0x2a2f3a, 0x1c212b).translateY(-dims.y / 2));
      setInfo(`${(geometry.attributes.position.count / 3).toLocaleString()} triangles · ${dims.x.toFixed(1)} × ${dims.y.toFixed(1)} × ${dims.z.toFixed(1)} (file units, usually mm). Drag to turn, scroll to zoom, right-drag to pan.`);
    }, undefined, () => setInfo("This mesh couldn't be read."));
    let frame = 0;
    const tick = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(tick); };
    tick();
    return () => { alive = false; cancelAnimationFrame(frame); watch.disconnect(); controls.dispose(); renderer.dispose(); el.removeChild(renderer.domElement); };
  }, [url]);
  return <div className="model-view"><div className="model-canvas" ref={box} /><div className="viewer-note">{info}</div></div>;
}

export function TableViewer({ sheets }: { sheets: { name: string; rows: string[][]; more: boolean }[] }) {
  const [i, setI] = useState(0);
  const sheet = sheets[i] ?? { name: "", rows: [], more: false };
  const width = Math.max(0, ...sheet.rows.map((r) => r.length));
  return (
    <div className="table-view">
      {sheets.length > 1 && <div className="viewer-tabs">{sheets.map((s, k) => <button key={s.name + k} className={k === i ? "on" : ""} onClick={() => setI(k)}>{s.name}</button>)}</div>}
      <div className="table-scroll">
        <table className="sheet">
          <thead><tr><th />{Array.from({ length: width }, (_, c) => <th key={c}>{String.fromCharCode(65 + (c % 26)).repeat(Math.floor(c / 26) + 1)}</th>)}</tr></thead>
          <tbody>{sheet.rows.map((row, r) => <tr key={r}><th>{r + 1}</th>{Array.from({ length: width }, (_, c) => <td key={c}>{row[c] ?? ""}</td>)}</tr>)}</tbody>
        </table>
      </div>
      {sheet.more && <div className="viewer-note">Only the first 1000 rows are shown.</div>}
    </div>
  );
}

function JsonNode({ name, value, depth }: { name: string; value: unknown; depth: number }) {
  const [open, setOpen] = useState(depth < 1);
  const isObj = value !== null && typeof value === "object";
  if (!isObj) {
    const kind = value === null ? "null" : typeof value;
    return <div className="json-row" style={{ paddingLeft: depth * 16 }}>{name && <span className="json-key">{name}: </span>}<span className={`json-${kind}`}>{kind === "string" ? JSON.stringify(value) : String(value)}</span></div>;
  }
  const entries = Array.isArray(value) ? value.map((v, k) => [String(k), v] as const) : Object.entries(value as Record<string, unknown>);
  const summary = Array.isArray(value) ? `[${entries.length}]` : `{${entries.length}}`;
  return (
    <div>
      <button className="json-row json-toggle" style={{ paddingLeft: depth * 16 }} onClick={() => setOpen((o) => !o)}>
        <span className="tree-caret">{open ? "▾" : "▸"}</span>{name && <span className="json-key">{name}: </span>}<span className="json-summary">{summary}</span>
      </button>
      {open && entries.slice(0, 500).map(([k, v]) => <JsonNode key={k} name={k} value={v} depth={depth + 1} />)}
      {open && entries.length > 500 && <div className="json-row viewer-note" style={{ paddingLeft: (depth + 1) * 16 }}>{entries.length - 500} more not shown.</div>}
    </div>
  );
}

/** A collapsible tree. A .jsonl file is one tree per line. */
export function JsonViewer({ text, lines }: { text: string; lines: boolean }) {
  const parsed = (() => {
    try {
      if (!lines) return { ok: true as const, value: JSON.parse(text) };
      return { ok: true as const, value: text.split("\n").filter((l) => l.trim()).map((l) => { try { return JSON.parse(l); } catch { return l; } }) };
    } catch (e) { return { ok: false as const, error: (e as Error).message }; }
  })();
  if (!parsed.ok) return <div className="viewer-note">This isn't valid JSON ({parsed.error}). Switch to Raw to see it as text.</div>;
  return <div className="json-view">{lines ? (parsed.value as unknown[]).map((v, k) => <JsonNode key={k} name={`line ${k + 1}`} value={v} depth={0} />) : <JsonNode name="" value={parsed.value} depth={0} />}</div>;
}

export function MarkdownViewer({ text }: { text: string }) {
  return <div className="md-view"><Rendered text={text} /></div>;
}
