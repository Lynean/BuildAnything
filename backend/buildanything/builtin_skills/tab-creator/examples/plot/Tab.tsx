import { Card, Grid, Status, Table, Plot, RunButton, useData } from "@ba/tab";

interface T { file: string; x: string; rows: number; series: { name: string; x: number[]; y: number[] }[]; stats: { column: string; min: number; max: number; mean: number; count: number }[] }

export default function Tab() {
  const { data } = useData();
  const tables: T[] = data?.output?.tables ?? [];
  return (
    <Grid>
      <Card title="Data" hint="Reads the project's CSV files, summarises each number column and plots the first few against the first column.">
        {data?.output ? <Status state={tables.length ? "good" : "warn"}>{tables.length} files with numbers</Status> : <Status state="warn">Not read yet</Status>}
        <RunButton label="Read again" />
      </Card>
      {tables.map((t) => (
        <Card key={t.file} title={t.file.split("/").pop()} wide hint={`${t.file}: ${t.rows} rows, plotted against ${t.x}.`}>
          {t.series.length > 0 && <Plot series={t.series} xLabel={t.x} yLabel="Value" />}
          <Table columns={["Column", "Min", "Max", "Mean", "Values"]} rows={t.stats.map((s) => [s.column, s.min, s.max, s.mean, s.count])} />
        </Card>
      ))}
    </Grid>
  );
}
