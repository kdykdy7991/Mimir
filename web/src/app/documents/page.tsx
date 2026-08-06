import type { Metadata } from "next";
import { DocumentsView } from "@/features/knowledge/documents-view";
export const metadata: Metadata = { title: "全部文档" };
export default function DocumentsPage() { return <DocumentsView />; }
