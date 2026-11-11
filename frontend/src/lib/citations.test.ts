import { describe, expect, it } from "vitest";
import { citationRefFromHref, extractCitationRefs, linkifyCitations } from "./citations";

describe("citations", () => {
  it("extracts unique refs in order, including grouped ones", () => {
    expect(extractCitationRefs("A [E1]. B [E3, E2] and again [E1][E4].")).toEqual(["E1", "E3", "E2", "E4"]);
    expect(extractCitationRefs("nothing [1] [Ex] here")).toEqual([]);
  });

  it("turns citations into one link per ref", () => {
    expect(linkifyCitations("Works locally [E1, E2].")).toBe("Works locally [E1](#cite-E1)[E2](#cite-E2).");
  });

  it("leaves code spans and fenced blocks alone", () => {
    const md = "Use `arr[E1]` here [E2].\n```\nx = data[E3]\n```\n";
    const out = linkifyCitations(md);
    expect(out).toContain("`arr[E1]`");
    expect(out).toContain("x = data[E3]");
    expect(out).toContain("[E2](#cite-E2)");
  });

  it("marks server-flagged unverified claims", () => {
    expect(linkifyCitations("Use `evil/backdoor` [unverified]. Fine [E1].")).toBe("Use `evil/backdoor` [unverified](#unverified). Fine [E1](#cite-E1).");
  });

  it("recognises citation hrefs only", () => {
    expect(citationRefFromHref("#cite-E12")).toBe("E12");
    expect(citationRefFromHref("#cite-X")).toBeNull();
    expect(citationRefFromHref("https://example.com/#cite-E1")).toBeNull();
    expect(citationRefFromHref(undefined)).toBeNull();
  });
});
