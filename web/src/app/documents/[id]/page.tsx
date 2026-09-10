import type { Metadata } from "next";
import { DocumentDetailView } from "@/features/knowledge/document-detail-view";

export const metadata: Metadata = { title: "文档详情" };

export default async function DocumentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <DocumentDetailView id={id} />;
}
