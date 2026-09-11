import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CollectionsView } from "./collections-view";

describe("CollectionsView card navigation", () => {
  it("opens a collection through the whole card without a detail button", async () => {
    render(<CollectionsView />);

    const cardLink = await screen.findByRole("link", { name: /打开知识库/ });
    expect(cardLink).toHaveAttribute("href", expect.stringMatching(/^\/collections\//));
    expect(screen.queryByText("查看详情")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /删除/ })).toBeInTheDocument();
  });
});
