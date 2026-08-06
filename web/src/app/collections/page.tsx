import type { Metadata } from "next";
import { CollectionsView } from "@/features/knowledge/collections-view";
export const metadata: Metadata = { title: "知识库" };
export default function CollectionsPage() { return <CollectionsView />; }
