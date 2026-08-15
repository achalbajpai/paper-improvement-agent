"use client";

import Link from "next/link";
import { Suspense, use, useCallback, useEffect, useRef, useState } from "react";

import { EditComposer, EditProposal, useEditing } from "@/components/paper/EditPanel";
import { ExportPanel } from "@/components/paper/ExportPanel";
import { ManuscriptView } from "@/components/paper/ManuscriptView";
import { ParseQualityPanel } from "@/components/paper/ParseQualityPanel";
import { ReferencesPanel } from "@/components/paper/ReferencesPanel";
import { ReviewPanel } from "@/components/paper/ReviewPanel";
import { StylePicker } from "@/components/paper/StylePicker";
import { PageHeader } from "@/components/PageHeader";
import { Failed, Loading } from "@/components/States";
import { Button } from "@/components/ui/Button";
import { Card, CardMeta, CardTitle } from "@/components/ui/Card";
import { StatusLabel } from "@/components/ui/StatusLabel";
import { Working } from "@/components/ui/Working";
import { TabPanel, Tabs, type TabDefinition } from "@/components/ui/Tabs";
import { api, type Finding } from "@/lib/api/client";
import { ERROR_LABEL, humanise } from "@/lib/labels";
import { useTabParam } from "@/lib/tabs";
import { useAction, useAsync } from "@/lib/useAsync";

const TAB_IDS = ["manuscript", "review", "references", "parse", "proposal", "export"] as const;
type TabId = (typeof TAB_IDS)[number];

export default function PaperPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return (
    <Suspense
      fallback={
        <Shell>
          <Loading />
        </Shell>
      }
    >
      <Workspace id={id} />
    </Suspense>
  );
}

function Workspace({ id }: { id: string }) {
  const paper = useAsync(() => api.getPaper(id), [id]);
  const capabilities = useAsync(() => api.ready(), []);
  const [findings, setFindings] = useState<Finding[]>([]);
  const [reviewStale, setReviewStale] = useState(false);
  const onFindings = useCallback((next: Finding[], stale: boolean) => {
    setFindings(next);
    setReviewStale(stale);
  }, []);
  const {
    tab,
    anchor,
    select: selectTab,
    selectWithAnchor,
  } = useTabParam<TabId>(TAB_IDS, "manuscript");
  const viewInManuscript = useCallback(
    (finding: Finding) => {
      if (!finding.claim.paragraph_id) return;
      selectWithAnchor("manuscript", finding.claim.paragraph_id);
    },
    [selectWithAnchor],
  );
  const editing = useEditing(id, paper.reload);
  const revisionKey = paper.data?.current_revision_id ?? "";
  const manuscript = useAsync(() => api.getManuscript(id), [id, revisionKey]);

  const parse = useAction(async (key: string) => {
    const updated = await api.parsePaper(id, key);
    paper.setData(updated);
    await paper.reload();
    return updated;
  });

  const started = useRef<string | null>(null);
  useEffect(() => {
    if (paper.data?.status !== "UPLOADED") return;
    if (started.current === id) return;
    started.current = id;
    void parse.run();
  }, [paper.data?.status, id, parse]);

  if (paper.loading && !paper.data)
    return (
      <Shell>
        <Loading />
      </Shell>
    );
  if (paper.failure) {
    return (
      <Shell>
        <Failed failure={paper.failure} onRetry={paper.reload} />
      </Shell>
    );
  }
  if (!paper.data)
    return (
      <Shell>
        <Loading />
      </Shell>
    );

  const data = paper.data;
  const parsed = data.status === "PARSED";
  const shownTab: TabId = tab === "proposal" && !editing.shown ? "manuscript" : tab;

  return (
    <Shell>
      <PageHeader
        title={data.title ?? data.original_filename}
        subtitle={data.original_filename}
        actions={
          <>
            <StatusLabel tone={data.status === "PARSE_FAILED" ? "block" : "neutral"}>
              {humanise(data.status)}
            </StatusLabel>
            <Link href="/" className="text-secondary text-text-muted underline underline-offset-2">
              All papers
            </Link>
          </>
        }
      />

      {parsed && !data.citation_style ? (
        <div className="mt-4">
          <StylePicker paper={data} onChange={paper.setData} />
        </div>
      ) : null}

      {data.status === "UPLOADED" ? (
        <Card className="mt-6">
          <CardTitle>Reading your paper</CardTitle>
          <CardMeta className="mt-1">Finding sections, citations, and references.</CardMeta>
          {parse.failure ? (
            <>
              <Failed className="mt-4" failure={parse.failure} />
              <Button className="mt-3" variant="primary" onClick={() => void parse.run()}>
                Try again
              </Button>
            </>
          ) : (
            <Working className="mt-4" label="Parsing" />
          )}
        </Card>
      ) : null}

      {data.status === "PARSE_FAILED" ? (
        <div className="mt-6">
          <Failed
            failure={{
              code: data.failure_code ?? "PARSER_FAILED",
              message:
                ERROR_LABEL[data.failure_code ?? "PARSER_FAILED"] ?? "We could not read this PDF.",
            }}
            onRetry={() => void parse.run()}
          />
        </div>
      ) : null}

      {parsed ? (
        <>
          <div className="mt-6 border-b border-border pb-2">
            <Tabs
              tabs={tabsFor(findings.length, reviewStale, editing.shown?.state ?? null)}
              active={shownTab}
              onSelect={selectTab}
              label="Paper"
            />
          </div>

          <div className="mt-6">
            <TabPanel id="manuscript" active={shownTab}>
              <div className="flex flex-col gap-8 lg:flex-row-reverse lg:items-start">
                <aside className="lg:sticky lg:top-6 lg:w-64 lg:shrink-0">
                  <EditComposer
                    editing={editing}
                    sections={manuscript.data?.sections ?? []}
                    available={capabilities.data?.llm_configured ?? true}
                    onProposed={() => selectTab("proposal")}
                  />
                </aside>
                <div className="min-w-0 flex-1">
                  <ManuscriptView manuscript={manuscript} findings={findings} target={anchor} />
                </div>
              </div>
            </TabPanel>

            <TabPanel id="review" active={shownTab}>
              <ReviewPanel
                paperId={id}
                currentRevisionId={data.current_revision_id ?? null}
                onFindings={onFindings}
                available={capabilities.data?.llm_configured ?? true}
                capabilities={capabilities.data ?? null}
                onViewFinding={viewInManuscript}
                onReviewed={manuscript.reload}
              />
            </TabPanel>

            <TabPanel id="proposal" active={shownTab}>
              <EditProposal editing={editing} />
            </TabPanel>

            <TabPanel id="references" active={shownTab}>
              <ReferencesPanel manuscript={manuscript} />
            </TabPanel>

            <TabPanel id="parse" active={shownTab}>
              {data.parse_quality ? <ParseQualityPanel quality={data.parse_quality} /> : null}
            </TabPanel>

            <TabPanel id="export" active={shownTab}>
              {data.citation_style ? (
                <div className="mb-6">
                  <StylePicker paper={data} onChange={paper.setData} />
                </div>
              ) : null}
              <ExportPanel
                paperId={id}
                revisionKey={`${data.current_revision_id}:${data.citation_style}`}
                available={capabilities.data?.pandoc ?? true}
              />
            </TabPanel>
          </div>
        </>
      ) : null}
    </Shell>
  );
}

function tabsFor(
  findings: number,
  stale: boolean,
  proposal: string | null,
): TabDefinition<TabId>[] {
  return [
    { id: "manuscript", label: "Manuscript" },
    {
      id: "review",
      label: stale ? "Review · earlier revision" : "Review",
      count: stale ? undefined : findings,
    },
    ...(proposal
      ? [
          {
            id: "proposal" as const,
            label:
              proposal === "AWAITING_DECISION" ? "Proposed edit · awaiting you" : "Proposed edit",
          },
        ]
      : []),
    { id: "references", label: "References" },
    { id: "parse", label: "Parse details" },
    { id: "export", label: "Export" },
  ];
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="mx-auto max-w-[1100px] px-6 py-8">{children}</main>;
}
