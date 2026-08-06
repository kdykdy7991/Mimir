import type { Metadata } from "next";

import { TraceExplorer } from "@/features/traces/trace-explorer";

export const metadata: Metadata = { title: "链路追踪" };

export default async function TracesPage({ searchParams }: { searchParams: Promise<{ id?: string; type?: string }> }) {
  const params = await searchParams;
  return (
    <div className="app-container space-y-6">
      <header>
        <div className="mb-3 flex items-center gap-2">
          <span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">Trace explorer</span>
          <span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span>
        </div>
        <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">链路追踪</h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">定位查询与文档处理链路中的耗时、Provider 配置和阶段异常。</p>
      </header>
      <TraceExplorer initialId={params.id ?? ""} initialType={params.type === "ingestion" ? "ingestion" : "query"} />
    </div>
  );
}
