import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import type { Match } from "@/api/types";
import { useCompareTray, useEvidenceViewer } from "@/state/stores";
import { makeRepo, renderApp } from "@/test/utils";
import { RepoRow } from "./RepoRow";

const match: Match = {
  chunk_id: 77,
  document_id: 5,
  repo_id: 1,
  path: "docs/embedded.md",
  kind: "doc",
  heading: "Embedded mode",
  start_line: 12,
  end_line: 30,
  snippet: "Runs as an «embedded» «vector» store, no server required.",
  commit_sha: "abcdef1234567890",
  indexed_at: new Date().toISOString(),
  html_url: "https://github.com/qdrant/qdrant/blob/abcdef1/docs/embedded.md#L12-L30",
  score: 1,
  source: "both",
};

describe("RepoRow (search result)", () => {
  beforeEach(() => {
    useEvidenceViewer.setState({ target: null });
    useCompareTray.setState({ ids: [], names: {} });
  });

  it("shows identity, facts, topics and highlighted matches", () => {
    renderApp(
      <RepoRow
        repo={makeRepo()}
        rank={3}
        matches={[match]}
        highlights={{ name: "«qdrant»", description: "«Vector» database and vector search engine." }}
        signals={{ lexical_rank: 2, semantic_rank: 1, fused_score: 0.0325, rerank_score: 0.91, prior: 1.03, name_match: true }}
        score={0.9373}
        explain
      />,
    );
    const row = screen.getByRole("article");
    expect(within(row).getByText("3")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: /^qdrant\s*\/\s*qdrant$/ })).toHaveAttribute("href", "/repo/1");
    expect(within(row).getByText("27k")).toBeInTheDocument();
    expect(within(row).getByText("Apache-2.0")).toBeInTheDocument();
    expect(within(row).getByText("vector-database")).toBeInTheDocument();
    const marks = [...row.querySelectorAll("mark.hit")].map((m) => m.textContent);
    expect(marks).toEqual(["qdrant", "Vector", "embedded", "vector"]);
    // citation: path, line range and short commit
    expect(within(row).getByText("docs/embedded.md")).toBeInTheDocument();
    expect(within(row).getByText("L12–30")).toBeInTheDocument();
    expect(within(row).getByText("@abcdef1")).toBeInTheDocument();
    // explain line
    expect(within(row).getByText(/lexical #2/)).toHaveTextContent("semantic #1");
    expect(within(row).getByText(/rerank 0\.910/)).toBeInTheDocument();
  });

  it("opens the cited passage in the evidence viewer and links to GitHub", async () => {
    renderApp(<RepoRow repo={makeRepo()} matches={[match]} />);
    await userEvent.click(screen.getByTitle("Open the cited passage"));
    expect(useEvidenceViewer.getState().target).toEqual({ type: "chunk", chunkId: 77, label: "Matched passage" });
    const gh = screen.getByRole("link", { name: "Open this passage on GitHub" });
    expect(gh).toHaveAttribute("href", match.html_url);
    expect(gh).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("adds and removes the repository from the comparison tray", async () => {
    renderApp(<RepoRow repo={makeRepo()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add to comparison" }));
    expect(useCompareTray.getState().ids).toEqual([1]);
    await userEvent.click(screen.getByRole("button", { name: "Remove from comparison" }));
    expect(useCompareTray.getState().ids).toEqual([]);
  });

  it("flags unstarred and inaccessible repositories in words", () => {
    renderApp(<RepoRow repo={makeRepo({ star_state: "unstarred", access_state: "not_found", maintenance: "archived", is_archived: true })} />);
    expect(screen.getByText("unstarred")).toBeInTheDocument();
    expect(screen.getByText("no longer accessible")).toBeInTheDocument();
    expect(screen.getByText("Archived")).toBeInTheDocument();
  });
});
