import type { Metadata } from "next";
import { CollectionDetailView } from "@/features/knowledge/collection-detail-view";
export const metadata: Metadata = { title: "知识库详情" };
export default async function CollectionDetailPage({ params }: { params: Promise<{ id: string }> }) { const { id } = await params; return <CollectionDetailView id={id} />; }
