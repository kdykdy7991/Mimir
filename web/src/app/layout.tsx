import type { Metadata } from "next";

import { AppShell } from "@/components";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "SKDY RAG",
    template: "%s · SKDY RAG",
  },
  description: "可插拔、可观测的 RAG MCP Server 管理与检索调试控制台。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
