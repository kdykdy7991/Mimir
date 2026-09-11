import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { CollectionDetailView } from "./collection-detail-view";
import { collection } from "@/test/mocks/fixtures";
import { mockServer } from "@/test/mocks/server";

describe("CollectionDetailView", () => {
  it("renders the breadcrumb without a duplicate back link", async () => {
    render(<CollectionDetailView id={collection.id} />);
    const nav = await screen.findByRole("navigation", { name: "面包屑" });
    expect(nav).toHaveTextContent("知识库");
    expect(
      await screen.findByRole("heading", { name: collection.name }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("link", { name: /返回知识库/ }),
    ).not.toBeInTheDocument();
  });

  it("exposes the collection stat line under the description", async () => {
    render(<CollectionDetailView id={collection.id} />);
    expect(
      await screen.findByText(
        new RegExp(`${collection.document_count ?? 1} 份文档`),
      ),
    ).toBeInTheDocument();
  });

  it("opens the edit dialog when 编辑知识库 is clicked", async () => {
    const user = userEvent.setup();
    render(<CollectionDetailView id={collection.id} />);
    await user.click(
      await screen.findByRole("button", { name: /编辑知识库/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "编辑知识库描述" }),
    ).toBeInTheDocument();
  });

  it("opens the upload dialog when the header upload button is clicked", async () => {
    const user = userEvent.setup();
    render(<CollectionDetailView id={collection.id} />);
    await user.click(
      await screen.findByRole("button", { name: /^上传文档$/ }),
    );
    expect(
      await screen.findByText(
        "文档将上传至「测试知识库」，并自动完成解析与索引。",
      ),
    ).toBeInTheDocument();
  });

  it("renders an error state with retry when the collection fails to load", async () => {
    mockServer.use(
      http.get("/api/v1/collections/:id", () =>
        HttpResponse.json(
          {
            error: {
              code: "NOT_FOUND",
              message: "知识库不存在",
              details: {},
              request_id: "req-1",
            },
          },
          { status: 404 },
        ),
      ),
    );
    render(<CollectionDetailView id="missing-id" />);
    expect(
      await screen.findByText(/无法加载知识库/),
    ).toBeInTheDocument();
    expect(screen.getByText(/NOT_FOUND/)).toBeInTheDocument();
  });
});
