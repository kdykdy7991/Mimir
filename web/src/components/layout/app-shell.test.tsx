import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppShell } from "./app-shell";

// AppShell reads the route from next/navigation and renders links via
// next/link; both need light fakes in a jsdom test (no Next router).
const { usePathnameMock } = vi.hoisted(() => ({
  usePathnameMock: vi.fn(() => "/overview"),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => usePathnameMock(),
}));

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: {
    children: React.ReactNode;
    href: string;
  }) => (
    <a href={typeof href === "string" ? href : "/"} {...props}>
      {children}
    </a>
  ),
}));

describe("AppShell", () => {
  it("renders children and current page title", () => {
    usePathnameMock.mockReturnValue("/overview");
    render(
      <AppShell>
        <p>页面内容</p>
      </AppShell>,
    );
    // "系统总览" appears twice: the sidebar nav link and the header title.
    expect(screen.getAllByText("系统总览").length).toBeGreaterThan(0);
    expect(screen.getByText("页面内容")).toBeInTheDocument();
  });

  it("marks the active route link", () => {
    usePathnameMock.mockReturnValue("/collections");
    render(<AppShell>body</AppShell>);
    const active = screen.getByRole("link", { name: "知识库" });
    expect(active).toHaveAttribute("aria-current", "page");
    const inactive = screen.getByRole("link", { name: "检索调试" });
    expect(inactive).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("link", { name: "全部文档" })).not.toBeInTheDocument();
  });
});
