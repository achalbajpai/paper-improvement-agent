"use client";

import { useState } from "react";

import { Failed, Loading } from "@/components/States";
import { Mono } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { BlockingNotice, StatusLabel } from "@/components/ui/StatusLabel";
import { api, type ExportRun } from "@/lib/api/client";
import { cn } from "@/lib/cn";
import { plural } from "@/lib/labels";
import { useAction, useAsync } from "@/lib/useAsync";

export function ExportPanel({
  paperId,
  revisionKey,
  available = true,
}: {
  paperId: string;
  revisionKey: string;
  available?: boolean;
}) {
  const preflight = useAsync(() => api.exportPreflight(paperId), [paperId, revisionKey]);
  const [acknowledged, setAcknowledged] = useState<Set<string>>(new Set());
  const [run, setRun] = useState<ExportRun | null>(null);

  const start = useAction(async (key: string) => {
    const result = await api.createExport(paperId, Array.from(acknowledged), key);
    setRun(result);
    return result;
  }, revisionKey);

  const required = preflight.data?.warnings ?? [];
  const outstanding = required.filter((warning) => !acknowledged.has(warning.id));
  const canAcknowledge = preflight.data?.can_export ?? false;

  return (
    <section>
      <h2 className="text-section-title">Export</h2>
      <CardMeta className="mt-1">
        Your text, citations, and references carry over. Fonts and page layout do not. This is a
        re-typeset paper, not a copy of your PDF.
      </CardMeta>

      {preflight.loading ? <Loading className="mt-3" /> : null}
      {preflight.failure ? (
        <Failed className="mt-3" failure={preflight.failure} onRetry={preflight.reload} />
      ) : null}

      {preflight.data ? (
        <Card className="mt-3">
          <div className="flex items-baseline justify-between gap-4">
            <CardTitle>
              {preflight.data.citation_style
                ? `Formatted as ${preflight.data.citation_style}`
                : "No citation style chosen"}
            </CardTitle>
            <StatusLabel tone={preflight.data.can_export ? "pass" : "block"}>
              {preflight.data.can_export ? "Ready to export" : "Cannot export"}
            </StatusLabel>
          </div>

          {(preflight.data.blockers ?? []).length > 0 ? (
            <div className="mt-3">
              <BlockingNotice title="This cannot be exported cleanly">
                <ul className="space-y-2">
                  {preflight.data.blockers?.map((blocker) => (
                    <li key={blocker.code}>
                      {blocker.message}
                      {blocker.subject_ids && blocker.subject_ids.length > 0 ? (
                        <span className="ml-2 font-mono text-label text-text-muted">
                          {blocker.subject_ids.join(", ")}
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              </BlockingNotice>
            </div>
          ) : null}

          {required.length > 0 ? (
            <div
              className={cn(
                "mt-4 rounded-card border p-4",
                canAcknowledge ? "border-warn/30 bg-warn-tint" : "border-border bg-bg opacity-70",
              )}
            >
              <p
                className={cn("text-card-title", canAcknowledge ? "text-warn" : "text-text-muted")}
              >
                {canAcknowledge
                  ? "Export means accepting:"
                  : "If this could be exported, it would mean accepting:"}
              </p>
              <ul className="mt-3 space-y-3">
                {required.map((warning) => (
                  <li key={warning.id}>
                    <label className="flex items-start gap-3">
                      <input
                        type="checkbox"
                        className="mt-1 accent-accent"
                        disabled={!canAcknowledge}
                        checked={acknowledged.has(warning.id)}
                        onChange={() =>
                          setAcknowledged((current) => {
                            const next = new Set(current);
                            if (next.has(warning.id)) next.delete(warning.id);
                            else next.add(warning.id);
                            return next;
                          })
                        }
                      />
                      <span className="text-secondary">
                        {warning.message}
                        {warning.subject_ids && warning.subject_ids.length > 0 ? (
                          <span className="ml-1 text-text-muted">
                            (<Mono>{warning.subject_ids.slice(0, 6).join(", ")}</Mono>
                            {warning.subject_ids.length > 6 ? " …" : ""})
                          </span>
                        ) : null}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="mt-4 flex items-center gap-3">
            <Button
              variant="primary"
              disabled={
                !preflight.data.can_export || outstanding.length > 0 || start.pending || !available
              }
              onClick={() => void start.run()}
            >
              {start.pending ? "Rendering…" : "Export"}
            </Button>
            {outstanding.length > 0 ? (
              <span className="text-secondary text-text-muted">
                {plural(outstanding.length, "consequence")} left to acknowledge.
              </span>
            ) : null}
          </div>

          {start.failure ? <Failed className="mt-4" failure={start.failure} /> : null}
          {run ? <Artifacts run={run} /> : null}
        </Card>
      ) : null}
    </section>
  );
}

function Artifacts({ run }: { run: ExportRun }) {
  if (run.status === "FAILED") {
    return (
      <Failed
        className="mt-4"
        failure={{
          code: run.failure_code ?? "RENDER_FAILED",
          message: "The export did not finish. No files were written.",
        }}
      />
    );
  }

  return (
    <div className="mt-4">
      <p className="text-label uppercase text-text-muted">Files</p>
      <ul className="mt-2 space-y-2">
        {(run.artifacts ?? []).map((artifact) => (
          <li
            key={artifact.name}
            className="flex items-center justify-between gap-4 rounded-card border border-border px-3 py-2"
          >
            <Mono>{artifact.name}</Mono>
            <div className="flex items-center gap-3">
              <span className="tabular text-label text-text-muted">
                {(artifact.size_bytes / 1024).toFixed(0)} KB
              </span>
              <a
                href={api.artifactUrl(artifact.href)}
                className="inline-flex min-h-[24px] items-center px-1 text-secondary text-accent underline underline-offset-2"
                aria-label={`Download ${artifact.name}`}
                download
              >
                Download
              </a>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
