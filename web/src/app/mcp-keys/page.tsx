import type { Metadata } from "next";
import { MCPKeysView } from "@/features/system/mcp-keys-view";

export const metadata: Metadata = { title: "API Key 管理" };
export default function MCPKeysPage() { return <MCPKeysView />; }
