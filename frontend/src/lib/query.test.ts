import { describe, expect, it } from "vitest";
import { hasOperators, matchOperators, replaceToken, tokenAt, tokenizeQuery } from "./query";

describe("tokenizeQuery", () => {
  it("classifies terms, operators, phrases, exclusions and OR", () => {
    const tokens = tokenizeQuery('agent lang:python "vector search" -java OR stars:>1000');
    expect(tokens.map((t) => t.kind)).toEqual(["term", "operator", "phrase", "excluded", "or", "operator"]);
    expect(tokens[1]).toMatchObject({ operator: "lang", value: "python" });
    expect(tokens[2]).toMatchObject({ value: "vector search" });
    expect(tokens[3]).toMatchObject({ value: "java" });
    expect(tokens[5]).toMatchObject({ operator: "stars", value: ">1000" });
  });

  it("keeps a quoted operator value together", () => {
    const [token] = tokenizeQuery('collection:"Agent stack"');
    expect(token).toMatchObject({ kind: "operator", operator: "collection", value: "Agent stack" });
  });

  it("treats an unknown prefix as a plain term", () => {
    expect(tokenizeQuery("https://example.com foo:bar").map((t) => t.kind)).toEqual(["term", "term"]);
  });

  it("records token positions", () => {
    const tokens = tokenizeQuery("mcp  topic:rag");
    expect(tokens[1]).toMatchObject({ start: 5, end: 14 });
  });
});

describe("tokenAt / matchOperators / replaceToken", () => {
  it("finds the token under the caret", () => {
    expect(tokenAt("mcp la", 6)?.text).toBe("la");
    expect(tokenAt("mcp la", 1)?.text).toBe("mcp");
    expect(tokenAt("", 0)).toBeNull();
  });

  it("suggests operators by prefix, and nothing once a colon is typed", () => {
    expect(matchOperators("la").map((o) => o.op)).toEqual(["lang:"]);
    expect(matchOperators("st").map((o) => o.op)).toEqual(["stars:", "starred:"]);
    expect(matchOperators("lang:")).toEqual([]);
    expect(matchOperators("")).toEqual([]);
  });

  it("replaces only the token under the caret", () => {
    expect(replaceToken("mcp la server", 6, "lang:")).toEqual({ text: "mcp lang: server", caret: 9 });
  });

  it("appends when the caret is not on a token", () => {
    expect(replaceToken("mcp ", 4, "topic:rag")).toEqual({ text: "mcp topic:rag", caret: 13 });
    expect(replaceToken("", 0, "lang:")).toEqual({ text: "lang:", caret: 5 });
  });

  it("detects operator use", () => {
    expect(hasOperators("vector lang:rust")).toBe(true);
    expect(hasOperators("vector rust")).toBe(false);
  });
});
