"use client";

import { Button } from "@/components/ui/Button";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { StatusLabel } from "@/components/ui/StatusLabel";
import { api, type CitationStyle, type Paper } from "@/lib/api/client";
import { useAction } from "@/lib/useAsync";

const STYLES: CitationStyle[] = ["IEEE", "APA"];

export function StylePicker({
  paper,
  onChange,
}: {
  paper: Paper;
  onChange: (paper: Paper) => void;
}) {
  const set = useAction(async (_key: string, style: CitationStyle) => {
    const updated = await api.setCitationStyle(paper.id, style);
    onChange(updated);
    return updated;
  });

  return (
    <Card>
      <div className="flex items-baseline justify-between gap-4">
        <CardTitle as="h2">Citation style</CardTitle>
        {paper.citation_style ? (
          <StatusLabel tone="pass">{paper.citation_style} selected</StatusLabel>
        ) : (
          <StatusLabel tone="warn">Not chosen yet</StatusLabel>
        )}
      </div>

      <CardMeta className="mt-1">
        {paper.detected_citation_style
          ? `Detected as ${paper.detected_citation_style} with ${(
              paper.detected_style_confidence ?? "UNKNOWN"
            ).toLowerCase()} confidence. ${paper.detected_style_reason ?? ""}`
          : "We could not tell which style this paper uses. Pick one."}
      </CardMeta>

      <div className="mt-4 flex items-center gap-2">
        {STYLES.map((style) => (
          <Button
            key={style}
            variant={paper.citation_style === style ? "primary" : "secondary"}
            disabled={set.pending}
            onClick={() => void set.run(style)}
          >
            {style}
          </Button>
        ))}
      </div>

      {!paper.citation_style ? (
        <CardMeta className="mt-3">
          Export stays unavailable until you choose. IEEE and APA produce visibly different
          manuscripts, so this is not a default worth guessing.
        </CardMeta>
      ) : null}

      {set.failure ? <p className="mt-3 text-secondary text-block">{set.failure.message}</p> : null}
    </Card>
  );
}
