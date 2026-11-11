import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Markdown } from "./Markdown";

describe("Markdown (untrusted content)", () => {
  it("does not execute or render raw HTML from a README", () => {
    const { container } = render(<Markdown>{'# Title\n\n<script>window.pwned = 1</script>\n\n<img src="x" onerror="alert(1)">\n\n<iframe src="https://evil.example"></iframe>'}</Markdown>);
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("iframe")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
  });

  it("opens links in a new tab with noopener and drops unsafe protocols", () => {
    const { container } = render(<Markdown>{"[docs](https://example.com/docs) and [bad](javascript:alert(1))"}</Markdown>);
    const link = screen.getByRole("link", { name: "docs" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link.getAttribute("rel")).toContain("noopener");
    expect(link.getAttribute("rel")).toContain("noreferrer");
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });

  it("resolves relative links against the repository URL", () => {
    render(<Markdown baseUrl="https://github.com/o/r/blob/main/">{"[guide](docs/guide.md)"}</Markdown>);
    expect(screen.getByRole("link", { name: "guide" })).toHaveAttribute("href", "https://github.com/o/r/blob/main/docs/guide.md");
  });

  it("never fetches remote images; they become labelled links", () => {
    const md = "![build status](https://img.shields.io/badge/build-passing-green) ![](https://example.com/diagram.png)";
    const { container } = render(<Markdown>{md}</Markdown>);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByRole("link", { name: /build status/ })).toHaveAttribute("href", "https://img.shields.io/badge/build-passing-green");
    expect(screen.getByRole("link", { name: /^image$/ })).toHaveAttribute("href", "https://example.com/diagram.png");
  });

  it("renders [E#] citations as buttons and flags unknown refs", async () => {
    const onCite = vi.fn();
    render(
      <Markdown onCite={onCite} knownRefs={new Set(["E1"])}>
        {"Runs embedded [E1]. Unsupported claim [E9]. Code `x[E1]` stays."}
      </Markdown>,
    );
    await userEvent.click(screen.getByRole("button", { name: "Evidence E1" }));
    expect(onCite).toHaveBeenCalledWith("E1");
    const unknown = screen.getByRole("button", { name: "Evidence E9" });
    await userEvent.click(unknown);
    expect(onCite).toHaveBeenCalledTimes(1);
    expect(unknown).toHaveAttribute("title", "E9 is not in the evidence list");
    expect(screen.getByText("x[E1]").tagName).toBe("CODE");
  });
});
