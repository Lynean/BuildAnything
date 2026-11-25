import { Card, Grid, Status, Table, RunButton, useData } from "@ba/tab";

interface Result { check: string; file: string; errors?: number; warnings?: number; error?: string; items?: { severity: string; rule: string; where: string }[] }

export default function Tab() {
  const { data } = useData();
  const results: Result[] = data?.output?.results ?? [];
  const errors = results.reduce((n, r) => n + (r.errors ?? 0), 0);
  const warnings = results.reduce((n, r) => n + (r.warnings ?? 0), 0);
  return (
    <Grid>
      <Card title="KiCad checks" hint={data?.output ? `Electrical (ERC) and design rule (DRC) checks by ${data.output.kicad}.` : "Runs KiCad's own rule checks on this project's schematics and boards."}>
        {data?.output ? <Status state={errors ? "bad" : warnings ? "warn" : "good"}>{errors} errors, {warnings} warnings</Status> : <Status state="warn">Not checked yet</Status>}
        <RunButton label="Check again" />
      </Card>
      {results.map((r) => (
        <Card key={r.check + r.file} title={`${r.check}: ${r.file.split("/").pop()}`} wide resizable hint={r.file}>
          {r.error ? <Status state="bad">{r.error}</Status> : (r.items?.length ? <Table columns={["Severity", "Rule", "Where"]} rows={r.items.map((v) => [v.severity, v.rule, v.where])} /> : <Status state="good">No problems found</Status>)}
        </Card>
      ))}
    </Grid>
  );
}
