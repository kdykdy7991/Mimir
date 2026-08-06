import type { Metadata } from "next";

import { QueryPlayground } from "@/features/playground/query-playground";

export const metadata: Metadata = { title: "检索调试" };

export default function PlaygroundPage() {
  return (
    <div className="app-container space-y-6">
      <header>
        <div className="mb-3 flex items-center gap-2">
          <span className="font-mono text-xs font-semibold tracking-[0.14em] text-primary uppercase">Query playground</span>
          <span className="rounded-full border border-success/20 bg-success/10 px-2.5 py-0.5 text-[0.6875rem] text-success">实时 API</span>
        </div>
        <h1 className="text-3xl font-semibold tracking-[-0.035em] sm:text-4xl">检索调试</h1>
        <p className="mt-2 max-w-2xl text-muted-foreground">验证 MCP Server 返回的检索上下文、引用依据与各阶段诊断；最终回答由调用端 Agent 生成。</p>
      </header>
      <QueryPlayground />
    </div>
  );
}
