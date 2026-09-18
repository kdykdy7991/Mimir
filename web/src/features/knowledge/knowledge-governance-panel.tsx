"use client";

import { useEffect, useState } from "react";
import { apiClient } from "@/api";
import { Button } from "@/components";
import type { DerivedArtifact, RevisionSummary, TagSuggestion } from "@/types";

export function KnowledgeGovernancePanel({ documentId, chunkId }: { documentId: string; chunkId: string }) {
  const [revisions, setRevisions] = useState<RevisionSummary[]>([]);
  const [suggestions, setSuggestions] = useState<TagSuggestion[]>([]);
  const [artifacts, setArtifacts] = useState<DerivedArtifact[]>([]);
  const [error, setError] = useState<string>();

  async function load(signal?: AbortSignal) {
    try {
      const [history, tags] = await Promise.all([
        apiClient.listChunkRevisions(documentId, chunkId, signal),
        apiClient.listTagSuggestions(documentId, signal),
      ]);
      setRevisions(history.revisions);
      setSuggestions(tags.suggestions.filter((item) => item.revision_id === history.revisions.find((row) => row.is_current)?.revision_id));
      const current = history.revisions.find((row) => row.is_current);
      setArtifacts(current ? (await apiClient.listDerivedArtifacts(current.revision_id, signal)).artifacts : []);
      setError(undefined);
    } catch (reason) {
      if (!signal?.aborted) setError(reason instanceof Error ? reason.message : "治理信息加载失败");
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    const task = window.setTimeout(() => void load(controller.signal), 0);
    return () => { window.clearTimeout(task); controller.abort(); };
  }, [documentId, chunkId]); // eslint-disable-line react-hooks/exhaustive-deps

  async function review(item: TagSuggestion, approve: boolean) {
    try {
      await apiClient.reviewTagSuggestion(item.suggestion_id, approve, item.existing_tag_id ?? undefined);
      await load();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "审核失败"); }
  }

  return <section className="space-y-4 border-t border-border pt-5">
    <div><h3 className="font-semibold">版本与增强治理</h3><p className="mt-1 text-xs text-muted-foreground">历史不可变；模型建议需人工审核，派生内容始终引用原 Chunk。</p></div>
    {error ? <p className="rounded-md bg-danger/10 px-3 py-2 text-xs text-danger">{error}</p> : null}
    <div><p className="text-xs font-semibold text-muted-foreground">版本历史</p><ul className="mt-2 space-y-2">{revisions.map((row) => <li className="flex items-center justify-between rounded-md bg-surface-muted px-3 py-2 text-xs" key={row.revision_id}><span className="truncate">{row.source} · {row.reason}</span><span className={row.is_current ? "font-semibold text-success" : "text-muted-foreground"}>{row.is_current ? "当前" : row.status}</span></li>)}</ul></div>
    <div><p className="text-xs font-semibold text-muted-foreground">待审核标签</p>{suggestions.filter((row) => row.status === "pending").length ? <ul className="mt-2 space-y-2">{suggestions.filter((row) => row.status === "pending").map((row) => <li className="rounded-md border border-border p-3 text-xs" key={row.suggestion_id}><div className="flex justify-between gap-2"><span className="font-medium">{row.suggested_name}</span><span>{Math.round(row.confidence * 100)}%</span></div><div className="mt-2 flex gap-2"><Button disabled={!row.existing_tag_id} onClick={() => void review(row, true)} size="sm">批准</Button><Button onClick={() => void review(row, false)} size="sm" variant="secondary">拒绝</Button></div></li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">当前版本没有待审核建议。</p>}</div>
    <div><p className="text-xs font-semibold text-muted-foreground">派生内容</p>{artifacts.length ? <ul className="mt-2 space-y-2">{artifacts.map((row) => <li className="rounded-md bg-surface-muted px-3 py-2 text-xs" key={row.artifact_id}><span className="font-medium">{row.kind === "summary" ? "摘要" : "合成问题"}</span><p className="mt-1 text-muted-foreground">{row.text}</p></li>)}</ul> : <p className="mt-2 text-xs text-muted-foreground">派生增强未启用或尚未生成。</p>}</div>
  </section>;
}
