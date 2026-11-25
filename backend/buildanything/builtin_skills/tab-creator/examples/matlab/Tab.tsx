import { Card, Grid, Status, Picture, Code, RunButton, useData } from "@ba/tab";

export default function Tab() {
  const { data } = useData();
  const out = data?.output;
  return (
    <Grid>
      <Card title="MATLAB analysis" hint={out ? `Ran ${out.script} in batch mode.` : "Runs the project's analysis script in MATLAB and keeps its figures. Needs a MATLAB licence on this computer."}>
        {out ? <Status state="good">{out.figures.length} figures</Status> : data?.ok === false ? <Status state="bad">It didn't run</Status> : <Status state="warn">Not run yet</Status>}
        <RunButton label="Run the analysis" />
      </Card>
      {out?.figures.map((f: string) => <Card key={f} title={f.replace(".png", "").replace("_", " ")} wide><Picture src={data!.images[f]} /></Card>)}
      {out?.printed && <Card title="What MATLAB printed" wide resizable><Code>{out.printed}</Code></Card>}
    </Grid>
  );
}
