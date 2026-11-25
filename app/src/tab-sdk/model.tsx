// "@ba/model": a 3D viewer for STL files in the project, for tool tabs that show CAD exports. Kept apart from "@ba/tab"
// so tabs that don't show models stay small.
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { useFile } from "./index";

/** Shows one STL file from the project. Drag to turn it, scroll to zoom. */
export function ModelViewer({ path, height = 360, color = "#c4c9d3" }: { path: string; height?: number; color?: string }) {
  const box = useRef<HTMLDivElement>(null);
  const { file, error } = useFile(path);
  const [size, setSize] = useState("");
  useEffect(() => {
    const el = box.current;
    if (!el || !(file?.base64 || file?.text)) return;
    const geometry = new STLLoader().parse(file.base64 ? Uint8Array.from(atob(file.base64), (c) => c.charCodeAt(0)).buffer : file.text!);
    geometry.computeBoundingBox();
    const bb = geometry.boundingBox!;
    const dims = new THREE.Vector3();
    bb.getSize(dims);
    setSize(`${dims.x.toFixed(1)} × ${dims.y.toFixed(1)} × ${dims.z.toFixed(1)} (file units)`);
    geometry.center();
    const scene = new THREE.Scene();
    scene.background = new THREE.Color("#12161d");
    const width = el.clientWidth || 600;
    const camera = new THREE.PerspectiveCamera(40, width / height, 0.01, 1e6);
    const radius = dims.length() || 1;
    camera.position.set(radius, radius * 0.8, radius);
    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    el.appendChild(renderer.domElement);
    scene.add(new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color, metalness: 0.1, roughness: 0.7 })));
    scene.add(new THREE.HemisphereLight(0xffffff, 0x334455, 1.2));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(radius, radius * 2, radius);
    scene.add(sun);
    const controls = new OrbitControls(camera, renderer.domElement);
    let frame = 0;
    const loop = () => { controls.update(); renderer.render(scene, camera); frame = requestAnimationFrame(loop); };
    loop();
    const resize = new ResizeObserver(() => { const w = el.clientWidth || width; renderer.setSize(w, height); camera.aspect = w / height; camera.updateProjectionMatrix(); });
    resize.observe(el);
    return () => { cancelAnimationFrame(frame); resize.disconnect(); renderer.dispose(); geometry.dispose(); el.removeChild(renderer.domElement); };
  }, [file, height, color]);
  if (error) return <p className="ba-err">{error}</p>;
  return <div><div ref={box} style={{ width: "100%", height, borderRadius: 8, overflow: "hidden", border: "1px solid var(--line)" }} />{size && <p className="ba-hint">{path}: {size}</p>}</div>;
}
