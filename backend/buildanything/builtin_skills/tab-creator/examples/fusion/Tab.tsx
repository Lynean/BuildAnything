import { useState } from "react";
import { Card, Grid, Status, Table, Code, Choice, useFiles, useFile, useTab } from "@ba/tab";
import { ModelViewer } from "@ba/model";

// Part meshes inside an assembly export (folders called meshes) aren't exports the person made; leave them out.
const isExport = (path: string) => /\.(stl|step|stp)$/i.test(path) && !/\/meshes\//i.test(path);

export default function Tab() {
  const tab = useTab();
  const exports = [...(useFiles("mechanics", "*.st*") ?? [])].filter((f) => isExport(f.path)).sort((a, b) => b.modified - a.modified);
  const stls = exports.filter((f) => /\.stl$/i.test(f.path));
  const [picked, setPicked] = useState<string | null>(null);
  const shown = picked ?? stls[0]?.path ?? null;
  const scripts = [...(useFiles("handoffs/Mechanical", "*.py") ?? [])].sort((a, b) => b.modified - a.modified);
  const [script, setScript] = useState<string | null>(null);
  const { file } = useFile(script);
  return (
    <Grid>
      <Card title="Exports from Fusion" hint={`STL and STEP files exported into ${tab?.project.name ?? "this project"}, newest first. Fusion itself isn't connected to the app.`}>
        {exports.length === 0 ? <Status state="warn">No STL or STEP exports yet. In Fusion: File, Export, then save into the project's mechanics folder.</Status> : (
          <Table columns={["File", "Size", "Exported"]} rows={exports.slice(0, 50).map((f) => [f.path, `${Math.max(1, Math.round(f.size / 1024))} KB`, new Date(f.modified * 1000).toLocaleString()])} />
        )}
      </Card>
      <Card title="Scripts for you to run in Fusion" hint="Mechanical writes these. Run them from Fusion's Scripts and Add-Ins, then report back on Mechanical's Capability tab.">
        {scripts.length === 0 ? <p className="ba-hint">None yet.</p> : <Choice label="Script" value={script} onChange={setScript} options={[{ value: "", label: "Choose one" }, ...scripts.map((s) => ({ value: s.path, label: s.path.split("/").pop()! }))]} />}
        {file?.text && <Code>{file.text}</Code>}
      </Card>
      <Card title="3D view" wide resizable hint={stls.length ? "Drag to turn it, scroll to zoom. STEP files can't be shown here; export STL to view them." : "Export an STL from Fusion to see it here."}>
        {stls.length > 1 && <Choice label="Model" value={shown} onChange={setPicked} options={stls.map((f) => ({ value: f.path, label: f.path }))} />}
        {shown && <ModelViewer path={shown} />}
      </Card>
    </Grid>
  );
}
