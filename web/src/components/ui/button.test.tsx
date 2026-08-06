import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Button } from "./button";

describe("Button", () => {
  it("renders children with default primary variant", () => {
    render(<Button>保存</Button>);
    const button = screen.getByRole("button", { name: "保存" });
    expect(button).toBeInTheDocument();
    expect(button).not.toBeDisabled();
  });

  it("disables while loading and shows a spinner", () => {
    render(<Button loading>保存</Button>);
    const button = screen.getByRole("button", { name: "保存" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
  });

  it("fires onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>点击</Button>);
    await userEvent.click(screen.getByRole("button", { name: "点击" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("does not fire onClick when disabled", async () => {
    const onClick = vi.fn();
    render(
      <Button disabled onClick={onClick}>
        点击
      </Button>,
    );
    await userEvent.click(screen.getByRole("button", { name: "点击" }));
    expect(onClick).not.toHaveBeenCalled();
  });
});
