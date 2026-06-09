import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import GlobalSidebar from "../GlobalSidebar";

const messages: Record<string, string> = {
  workspace: "工作区",
  library: "主体库",
  playground: "创作台",
  settings: "设置",
  collapseSidebar: "收起侧边栏",
  expandSidebar: "展开侧边栏",
};

vi.mock("next-intl", () => ({
  useTranslations: () => (key: string) => messages[key] ?? key,
}));

describe("GlobalSidebar", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.location.hash = "";
  });

  it("collapses to icon-only navigation and expands again", () => {
    render(<GlobalSidebar activeTab="workspace" onTabChange={vi.fn()} />);

    const sidebar = screen.getByRole("complementary");
    expect(sidebar).toHaveClass("w-56");
    expect(screen.getByText("工作区")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "收起侧边栏" }));

    expect(sidebar).toHaveClass("w-[72px]");
    expect(screen.queryByText("工作区")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "工作区" })).toHaveAttribute("title", "工作区");
    expect(window.localStorage.getItem("lumenx-global-sidebar-collapsed")).toBe("true");

    fireEvent.click(screen.getByRole("button", { name: "展开侧边栏" }));

    expect(sidebar).toHaveClass("w-56");
    expect(screen.getByText("工作区")).toBeInTheDocument();
    expect(window.localStorage.getItem("lumenx-global-sidebar-collapsed")).toBe("false");
  });

  it("restores the collapsed preference from local storage", async () => {
    window.localStorage.setItem("lumenx-global-sidebar-collapsed", "true");

    render(<GlobalSidebar activeTab="workspace" onTabChange={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByRole("complementary")).toHaveClass("w-[72px]");
    });
    expect(screen.getByRole("button", { name: "展开侧边栏" })).toBeInTheDocument();
  });
});
