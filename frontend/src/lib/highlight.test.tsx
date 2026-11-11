import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { cleanSnippet, Highlighted, splitHighlights, stripHighlights } from "./highlight";

describe("splitHighlights", () => {
  it("splits «marked» spans from plain text", () => {
    expect(splitHighlights("a «vector» database for «AI»")).toEqual([
      { text: "a ", hit: false },
      { text: "vector", hit: true },
      { text: " database for ", hit: false },
      { text: "AI", hit: true },
    ]);
  });

  it("returns plain text untouched", () => {
    expect(splitHighlights("no markers")).toEqual([{ text: "no markers", hit: false }]);
    expect(splitHighlights("")).toEqual([]);
  });

  it("degrades gracefully on unbalanced markers", () => {
    expect(splitHighlights("broken «marker").map((s) => s.text).join("")).toBe("broken marker");
    expect(splitHighlights("stray » close")).toEqual([{ text: "stray  close", hit: false }]);
    expect(splitHighlights("«a» «»b").filter((s) => s.hit)).toEqual([{ text: "a", hit: true }]);
  });

  it("strips markers", () => {
    expect(stripHighlights("«MCP» server")).toBe("MCP server");
  });
});

describe("Highlighted", () => {
  it("renders hits as <mark> and never interprets text as HTML", () => {
    const { container } = render(<Highlighted text={'«agent» <img src=x onerror="alert(1)"> framework'} />);
    const marks = container.querySelectorAll("mark.hit");
    expect(marks).toHaveLength(1);
    expect(marks[0]!.textContent).toBe("agent");
    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
  });
});

describe("cleanSnippet", () => {
  it("removes Markdown noise but keeps hit markers and words", () => {
    const raw = "## Features - **«agents»** support ![build](https://img.shields.io/x) see [docs](docs/a.md) and `code`";
    expect(cleanSnippet(raw)).toBe("Features - «agents» support see docs and code");
  });

  it("turns table syntax into readable separators", () => {
    expect(cleanSnippet("| Component | Responsibility | |---|---| | core | «lifecycle», state |")).toBe("Component · Responsibility · core · «lifecycle», state");
  });
});
