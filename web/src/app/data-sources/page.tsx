import type { Metadata } from "next";
import { DataSourcesView } from "@/features/system/data-sources-view";

export const metadata: Metadata = { title: "数据源管理" };

export default function DataSourcesPage() {
  return <DataSourcesView />;
}
