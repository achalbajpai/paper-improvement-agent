"use client";

import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { StatusLabel } from "@/components/ui/StatusLabel";
import { Table, TD, TH, THead } from "@/components/ui/Table";
import type { ParseQuality } from "@/lib/api/client";
import { plural } from "@/lib/labels";

export function ParseQualityPanel({ quality }: { quality: ParseQuality }) {
  const { citations, references, blocks, linkage } = quality;

  return (
    <Card>
      <CardTitle as="h2">What the parser found</CardTitle>
      <CardMeta className="mt-1">
        {plural(quality.sections, "section")}, {plural(quality.paragraphs, "paragraph")},{" "}
        {quality.words.toLocaleString()} words.
      </CardMeta>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Metric label="Citation markers" value={citations.total}>
          {citations.linked} linked to a bibliography entry
          {citations.unlinked > 0 ? `, ${citations.unlinked} unlinked` : ""}
          {citations.clusters > 0 ? `, ${plural(citations.clusters, "grouped marker")}` : ""}.
        </Metric>

        <Metric label="References" value={references.total}>
          {references.complete} parsed completely
          {references.partial > 0 ? `, ${references.partial} partially` : ""}
          {references.raw_only > 0 ? `, ${references.raw_only} as raw text only` : ""}.
        </Metric>

        <Metric label="Figures, tables, equations" value={blocks.total}>
          {blocks.unrenderable > 0
            ? `${blocks.unrenderable} cannot be reproduced, which blocks a fidelity export.`
            : "All of it carries through to the export."}
        </Metric>

        <Metric label="Uncertain modifiers" value={citations.partial_modifiers}>
          {citations.partial_modifiers > 0
            ? "We could not read the page numbers or notes on these. They will not export exactly."
            : "We read every page number and note."}
        </Metric>
      </div>

      {citations.raw_only > 0 ? (
        <p className="mt-4 text-secondary text-text-muted">
          {plural(citations.raw_only, "marker")} kept as raw text because their internal structure
          could not be determined. They still render as the author wrote them.
        </p>
      ) : null}

      {linkage ? <LinkageTable linkage={linkage} /> : null}

      {blocks.unrenderable > 0 ? (
        <div className="mt-4 rounded-card border border-block/30 bg-block-tint p-3">
          <p className="text-secondary text-block">
            {plural(blocks.unrenderable, "figure")} declared an image the source PDF did not
            provide. This paper cannot be exported at full fidelity.
          </p>
          <p className="mt-1 font-mono text-label text-text-muted">
            {blocks.unrenderable_ids.join(", ")}
          </p>
        </div>
      ) : null}
    </Card>
  );
}

function Metric({
  label,
  value,
  children,
}: {
  label: string;
  value: number;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-label uppercase text-text-muted">{label}</p>
      <p className="tabular text-page-title">{value.toLocaleString()}</p>
      <p className="mt-1 text-secondary text-text-muted">{children}</p>
    </div>
  );
}

function LinkageTable({ linkage }: { linkage: NonNullable<ParseQuality["linkage"]> }) {
  const rows: Array<[string, number, string]> = [
    ["Independently checked", linkage.checked, "Citations we could check a second way."],
    ["Agreed", linkage.agreed, "The second check agreed."],
    ["Corrected", linkage.recovered + linkage.promoted, "The second check fixed these."],
    ["Downgraded", linkage.downgraded, "The second check could not confirm these."],
    ["Left uncertain", linkage.uncertain, "Left uncertain instead of guessed."],
  ];

  return (
    <div className="mt-5">
      <div className="flex items-baseline justify-between">
        <p className="text-card-title">Marker-to-reference linkage</p>
        <StatusLabel tone={linkage.uncertain > 0 ? "warn" : "pass"}>
          {(linkage.accuracy * 100).toFixed(1)}% agreement over {linkage.checked} checked
        </StatusLabel>
      </div>
      <CardMeta className="mt-1">
        Citation family detected as {linkage.family.toLowerCase().replace(/_/g, " ")}.
      </CardMeta>

      <Table className="mt-3">
        <THead>
          <tr>
            <TH>Outcome</TH>
            <TH className="w-20 text-right">Count</TH>
            <TH>Meaning</TH>
          </tr>
        </THead>
        <tbody>
          {rows.map(([label, value, meaning]) => (
            <tr key={label}>
              <TD>{label}</TD>
              <TD className="tabular text-right">{value}</TD>
              <TD className="text-text-muted">{meaning}</TD>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  );
}
