"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { PageHeader } from "@/components/PageHeader";
import { Failed, Loading } from "@/components/States";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { Dropzone } from "@/components/ui/Dropzone";
import { StatusLabel } from "@/components/ui/StatusLabel";
import { api } from "@/lib/api/client";
import { humanise } from "@/lib/labels";
import { useAction, useAsync } from "@/lib/useAsync";

export default function UploadPage() {
  const router = useRouter();
  const papers = useAsync(() => api.listPapers(), []);
  const capabilities = useAsync(() => api.ready(), []);

  const upload = useAction(async (key: string, file: File) => {
    const paper = await api.uploadPaper(file, key);
    router.push(`/papers/${paper.id}`);
    return paper;
  });

  return (
    <main className="mx-auto max-w-[860px] px-6 py-8">
      <PageHeader
        title="Paper Improvement Agent"
        subtitle="Peer review your paper against real academic search. Edit it by instruction. Approve every change."
      />

      {capabilities.data && !capabilities.data.llm_configured ? (
        <p className="mt-4 rounded-card border border-warn/30 bg-warn-tint p-3 text-secondary text-warn">
          No language model is configured. You can upload and parse, but review and editing will not
          run.
        </p>
      ) : null}

      <Card className="mt-6">
        <CardTitle>Upload a paper</CardTitle>
        <CardMeta className="mt-1">PDF, up to 50 MB.</CardMeta>

        <div className="mt-4">
          <Dropzone
            disabled={upload.pending}
            onFile={(file) => void upload.run(file)}
            label={upload.pending ? "Uploading…" : "Drop PDFs here or"}
            browseLabel={upload.pending ? "" : "browse"}
          />
        </div>

        {upload.failure ? (
          <p className="mt-3 text-secondary text-block">{upload.failure.message}</p>
        ) : null}
      </Card>

      <h2 className="mt-8 text-section-title">Papers</h2>
      {papers.loading ? <Loading className="mt-3" /> : null}
      {papers.failure ? (
        <Failed className="mt-3" failure={papers.failure} onRetry={papers.reload} />
      ) : null}

      {papers.data?.length === 0 ? <CardMeta className="mt-3">No papers yet.</CardMeta> : null}

      <ul className="mt-3 space-y-2">
        {papers.data?.map((paper) => (
          <li key={paper.id}>
            <Link
              href={`/papers/${paper.id}`}
              className="block rounded-card border border-border p-4 transition-colors duration-state hover:border-border-strong"
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-card-title">{paper.title ?? paper.original_filename}</span>
                <StatusLabel tone={paper.status === "PARSE_FAILED" ? "block" : "neutral"}>
                  {humanise(paper.status)}
                </StatusLabel>
              </div>
              <CardMeta className="mt-1">
                {[
                  paper.original_filename,
                  paper.citation_style ?? undefined,
                  new Date(paper.created_at).toLocaleString(),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </CardMeta>
              <p className="mt-1 font-mono text-label text-text-muted">{paper.id}</p>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
