import { Card, Grid, Stat, Status, Picture, Plot, RunButton, useData } from "@ba/tab";

export default function Tab() {
  const { data } = useData();
  const out = data?.output;
  return (
    <Grid>
      <Card title="MuJoCo model" hint={out ? `${out.model}, ${out.source}, ${out.seconds} s.` : "Collect the data to read the project's simulation."}>
        {out ? <Status state="good">Model loads</Status> : data?.ok === false ? <Status state="bad">Couldn't read it</Status> : <Status state="warn">Not collected yet</Status>}
        <RunButton />
      </Card>
      {out && (
        <Card title="What's in it" hint="Counted from the model file. Masses are the model's, which may be estimates.">
          <Grid>
            <Stat label="Bodies" value={out.facts.bodies} />
            <Stat label="Joints" value={out.facts.joints} />
            <Stat label="Motors" value={out.facts.motors} />
            <Stat label="Total mass" value={out.facts.mass_g} unit="g" />
          </Grid>
        </Card>
      )}
      {out && (
        <Card title="Pictures" wide hint={out.pictures_skipped ? `No pictures on this computer (${out.pictures_skipped}). The numbers still come from the model.` : "Start, middle and end of the run."}>
          <Grid>{out.pictures.map((p: { file: string; at_s: number }) => <Picture key={p.file} src={data!.images[p.file]} caption={`At ${p.at_s} s`} />)}</Grid>
        </Card>
      )}
      {out && out.series.length > 0 && (
        <Card title="How the heaviest parts moved" wide hint="Height of the three heaviest bodies over the run.">
          <Plot series={out.series} xLabel="Time (s)" yLabel="Height (mm)" />
        </Card>
      )}
    </Grid>
  );
}
