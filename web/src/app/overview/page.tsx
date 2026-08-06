import type { Metadata } from "next";
import { RagOverviewV2 } from "@/features/system/rag-overview-v2";

export const metadata: Metadata = { title: "RAG 总览" };
export default function OverviewPage() {
  return <div className="app-container">
    <RagOverviewV2 />
  </div>;
}
