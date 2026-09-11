import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { CollectionsView } from "./collections-view";
import { mockServer } from "@/test/mocks/server";

const api = "/api/v1";

function collectionsPage(items: object[], nextCursor: string | null = null) {
  return {
    items,
    page_info: { next_cursor: nextCursor, has_more: nextCursor !== null },
  };
}

describe("CollectionsView", () => {
  it("loads and renders real knowledge base data", async () => {
    render(<CollectionsView />);

    expect(await screen.findByText("测试知识库")).toBeInTheDocument();
    expect(screen.getByText(/MSW v0\.2 fixture/)).toBeInTheDocument();
    expect(screen.getByText(/共 1 个知识库/)).toBeInTheDocument();
    expect(screen.getByText(/1 文档/)).toBeInTheDocument();
    expect(screen.getByText(/12 片段/)).toBeInTheDocument();
    expect(screen.getByText(/更新于/)).toBeInTheDocument();
  });

  it("opens a collection through the whole card", async () => {
    render(<CollectionsView />);

    const cardLink = await screen.findByRole("link", {
      name: /打开知识库/,
    });
    expect(cardLink).toHaveAttribute(
      "href",
      expect.stringMatching(/^\/collections\//),
    );
    expect(screen.queryByText("查看详情")).not.toBeInTheDocument();
  });

  it("opens the delete confirm dialog instead of navigating when clicking the delete button", async () => {
    const user = userEvent.setup();
    render(<CollectionsView />);

    const deleteButton = await screen.findByRole("button", {
      name: /删除知识库/,
    });
    await user.click(deleteButton);

    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent("确认删除该知识库？");
    expect(dialog).toHaveTextContent("测试知识库");
    expect(dialog).toHaveTextContent("1 份文档");
    expect(dialog).toHaveTextContent(/无法恢复/);

    // The card link should still be present — clicking delete must not navigate.
    expect(
      screen.getByRole("link", { name: /打开知识库/ }),
    ).toBeInTheDocument();
  });

  it("filters collections by both name and description", async () => {
    const user = userEvent.setup();
    render(<CollectionsView />);

    const input = await screen.findByLabelText("搜索知识库");
    await user.type(input, "MSW");

    expect(screen.getByText("测试知识库")).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "fixture");

    expect(screen.getByText("测试知识库")).toBeInTheDocument();

    await user.clear(input);
    await user.type(input, "no-such-token");

    expect(screen.queryByText("测试知识库")).not.toBeInTheDocument();
    expect(screen.getByText("没有搜索结果")).toBeInTheDocument();
  });

  it("renders the empty knowledge base state when the API returns no items", async () => {
    mockServer.use(
      http.get(`${api}/collections`, () =>
        HttpResponse.json(collectionsPage([])),
      ),
    );
    render(<CollectionsView />);

    expect(await screen.findByText("暂无知识库")).toBeInTheDocument();
    expect(
      screen.getByText("创建知识库后即可上传文档并搭建检索。"),
    ).toBeInTheDocument();
    // The header keeps a primary "新建知识库" CTA, plus the empty state adds another.
    const createButtons = screen.getAllByRole("button", {
      name: /新建知识库/,
    });
    expect(createButtons.length).toBeGreaterThanOrEqual(2);
  });

  it("shows an explicit clear-search action in the no-results state", async () => {
    const user = userEvent.setup();
    render(<CollectionsView />);

    const input = await screen.findByLabelText("搜索知识库");
    await user.type(input, "完全不会命中");

    expect(await screen.findByText("没有搜索结果")).toBeInTheDocument();
    // The no-results empty state must not be the only path forward: only the
    // header's primary CTA is present, alongside the explicit clear-search
    // action.
    const createButtons = screen.queryAllByRole("button", {
      name: /新建知识库/,
    });
    expect(createButtons.length).toBe(1);

    await user.click(screen.getByRole("button", { name: /清除搜索/ }));

    expect(screen.queryByText("没有搜索结果")).not.toBeInTheDocument();
    expect(input).toHaveValue("");
  });

  it("sorts collections by name, update time and document count", async () => {
    const collections = [
      {
        id: "id-1",
        name: "Bravo",
        description: null,
        document_count: 2,
        chunk_count: 20,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-05T00:00:00.000Z",
      },
      {
        id: "id-2",
        name: "Alpha",
        description: null,
        document_count: 5,
        chunk_count: 50,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-10T00:00:00.000Z",
      },
      {
        id: "id-3",
        name: "Charlie",
        description: null,
        document_count: 0,
        chunk_count: 0,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-02T00:00:00.000Z",
      },
    ];
    mockServer.use(
      http.get(`${api}/collections`, () =>
        HttpResponse.json(collectionsPage(collections)),
      ),
    );
    const user = userEvent.setup();
    render(<CollectionsView />);

    await screen.findByText("Alpha");

    // Default sort = recent update: Alpha (Aug 10) > Bravo (Aug 5) > Charlie (Aug 2).
    expect(headings()).toEqual(["Alpha", "Bravo", "Charlie"]);

    await user.selectOptions(
      screen.getByLabelText("排序方式"),
      "updated_asc",
    );
    expect(headings()).toEqual(["Charlie", "Bravo", "Alpha"]);

    await user.selectOptions(
      screen.getByLabelText("排序方式"),
      "name_asc",
    );
    // zh-Hans-CN localeCompare is stable for ASCII: Alpha < Bravo < Charlie.
    expect(headings()).toEqual(["Alpha", "Bravo", "Charlie"]);

    await user.selectOptions(
      screen.getByLabelText("排序方式"),
      "document_count_desc",
    );
    expect(headings()).toEqual(["Alpha", "Bravo", "Charlie"]);
  });

  it("filters by status using the client-side populated/empty facet", async () => {
    const collections = [
      {
        id: "id-1",
        name: "已建空知识库",
        description: null,
        document_count: 0,
        chunk_count: 0,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-01T00:00:00.000Z",
      },
      {
        id: "id-2",
        name: "已建有内容知识库",
        description: null,
        document_count: 3,
        chunk_count: 30,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-01T00:00:00.000Z",
      },
    ];
    mockServer.use(
      http.get(`${api}/collections`, () =>
        HttpResponse.json(collectionsPage(collections)),
      ),
    );
    const user = userEvent.setup();
    render(<CollectionsView />);

    await screen.findByText("已建有内容知识库");

    await user.selectOptions(
      screen.getByLabelText("按状态筛选"),
      "empty",
    );
    expect(screen.queryByText("已建有内容知识库")).not.toBeInTheDocument();
    expect(screen.getByText("已建空知识库")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("按状态筛选"),
      "populated",
    );
    expect(screen.queryByText("已建空知识库")).not.toBeInTheDocument();
    expect(screen.getByText("已建有内容知识库")).toBeInTheDocument();

    await user.selectOptions(
      screen.getByLabelText("按状态筛选"),
      "all",
    );
    expect(screen.getByText("已建空知识库")).toBeInTheDocument();
    expect(screen.getByText("已建有内容知识库")).toBeInTheDocument();
  });

  it("applies line-clamp truncation to long names and descriptions", async () => {
    const longName =
      "非常非常长的知识库名称需要被截断为单行展示出来方便测试 line-clamp-1 的样式正确生效";
    const longDescription =
      "非常非常长的描述内容需要被截断为两行展示出来方便测试 line-clamp-2 的样式正确生效，".repeat(
        4,
      );
    mockServer.use(
      http.get(`${api}/collections`, () =>
        HttpResponse.json(
          collectionsPage([
            {
              id: "id-long",
              name: longName,
              description: longDescription,
              document_count: 1,
              chunk_count: 1,
              created_at: "2026-08-01T00:00:00.000Z",
              updated_at: "2026-08-01T00:00:00.000Z",
            },
          ]),
        ),
      ),
    );
    render(<CollectionsView />);

    const heading = await screen.findByRole("heading", {
      name: longName,
      level: 2,
    });
    expect(heading).toHaveAttribute("title", longName);
    expect(heading.className).toMatch(/line-clamp-1/);

    const description = screen.getByText(longDescription);
    expect(description.className).toMatch(/line-clamp-2/);
  });

  it("allows the card link to receive keyboard focus and activate navigation", async () => {
    const user = userEvent.setup();
    render(<CollectionsView />);

    const cardLink = await screen.findByRole("link", {
      name: /打开知识库/,
    });
    cardLink.focus();
    expect(cardLink).toHaveFocus();

    // The card link is wrapped in a clickable card; pressing Enter must follow the link.
    const navigate = vi.fn();
    cardLink.addEventListener("click", navigate);
    await user.keyboard("{Enter}");
    expect(navigate).toHaveBeenCalled();
  });

  it("exposes a load-more action when the server reports another page", async () => {
    const first = [
      {
        id: "id-1",
        name: "知识库 1",
        description: null,
        document_count: 0,
        chunk_count: 0,
        created_at: "2026-08-01T00:00:00.000Z",
        updated_at: "2026-08-01T00:00:00.000Z",
      },
    ];
    const second = [
      {
        id: "id-2",
        name: "知识库 2",
        description: null,
        document_count: 0,
        chunk_count: 0,
        created_at: "2026-08-02T00:00:00.000Z",
        updated_at: "2026-08-02T00:00:00.000Z",
      },
    ];
    const handler = http.get(`${api}/collections`, ({ request }) => {
      const url = new URL(request.url);
      if (url.searchParams.get("cursor") === "page-2") {
        return HttpResponse.json(collectionsPage(second, null));
      }
      return HttpResponse.json(collectionsPage(first, "page-2"));
    });
    mockServer.use(handler);

    const user = userEvent.setup();
    render(<CollectionsView />);

    expect(await screen.findByText("知识库 1")).toBeInTheDocument();
    expect(
      screen.getByText(/已加载部分，筛选仅覆盖当前已加载数据/),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "加载更多" }));

    await waitFor(() => {
      expect(screen.getByText("知识库 2")).toBeInTheDocument();
    });
    expect(
      screen.queryByText(/已加载部分，筛选仅覆盖当前已加载数据/),
    ).not.toBeInTheDocument();
  });
});

function headings() {
  return screen
    .getAllByRole("heading", { level: 2 })
    .map((node) => node.textContent ?? "");
}