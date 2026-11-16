import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { McpInfo } from "@/api/types";
import { makeEvidence, renderApp } from "@/test/utils";
import AgentAccessSettings, { toolParams } from "./AgentAccessSettings";

const api = vi.hoisted(() => ({ mcpInfo: vi.fn(), callTool: vi.fn() }));
vi.mock("@/api/client", () => ({ api }));

const tools = [
  { name: "search_repositories", description: "Search the library.", input_schema: { type: "object", properties: { query: { type: "string", description: "What to look for" }, limit: { anyOf: [{ type: "integer" }, { type: "null" }], default: 8 } }, required: ["query"] } },
  { name: "compare_repositories", description: "Compare repositories.", input_schema: { type: "object", properties: { repos: { type: "array", items: { type: "string" } } }, required: ["repos"] } },
];
const info = (over: Partial<McpInfo> = {}): McpInfo =>
  ({
    http_url: "http://127.0.0.1:8765/mcp",
    stdio_command: ["/opt/starindex/python", "-m", "starindex.cli", "mcp"],
    claude_code_http: "claude mcp add --transport http starindex http://127.0.0.1:8765/mcp",
    claude_code_stdio: "claude mcp add starindex -- /opt/starindex/python -m starindex.cli mcp",
    desktop_config: { mcpServers: { starindex: { command: "/opt/starindex/python", args: ["-m", "starindex.cli", "mcp"] } } },
    requires_token: false,
    tools,
    ...over,
  }) as McpInfo;

beforeEach(() => {
  api.mcpInfo.mockReset();
  api.callTool.mockReset();
  api.mcpInfo.mockResolvedValue(info());
});

describe("toolParams", () => {
  it("reads names, types and required markers from the JSON schema", () => {
    expect(toolParams(tools[0] as never)).toEqual([
      { name: "query", type: "string", required: true, description: "What to look for" },
      { name: "limit", type: "integer", required: false, description: "" },
    ]);
    expect(toolParams(tools[1] as never)[0]).toMatchObject({ name: "repos", type: "array", required: true });
  });
});

describe("AgentAccessSettings", () => {
  it("renders the connection commands, the desktop config and the tool list", async () => {
    renderApp(<AgentAccessSettings />);
    expect(await screen.findByTestId("copy-claude-code-http-")).toHaveTextContent("claude mcp add --transport http starindex http://127.0.0.1:8765/mcp");
    expect(screen.getByTestId("copy-claude-code-stdio-")).toHaveTextContent("claude mcp add starindex -- /opt/starindex/python -m starindex.cli mcp");
    const desktop = screen.getByTestId("copy-claude-desktop-config");
    expect(JSON.parse(desktop.textContent!)).toEqual(info().desktop_config);
    expect(desktop.textContent).toContain("\n"); // pretty-printed
    expect(screen.getByText(/keyword search only/)).toBeInTheDocument();
    expect(screen.queryByTestId("mcp-token-note")).toBeNull();
    const list = screen.getByTestId("tool-list");
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(within(list).getByText("Search the library.")).toBeInTheDocument();
    expect(within(list).getAllByLabelText("required")).toHaveLength(2);
  });

  it("explains the bearer token when one is required", async () => {
    api.mcpInfo.mockResolvedValue(info({ requires_token: true }));
    renderApp(<AgentAccessSettings />);
    expect(await screen.findByTestId("mcp-token-note")).toHaveTextContent("Authorization: Bearer <token>");
  });

  it("runs a tool with the prefilled arguments and shows summary and evidence", async () => {
    api.callTool.mockResolvedValue({ ok: true, summary: "1 repositories for 'vector database':\n- qdrant/qdrant", evidence: [makeEvidence()], repo_ids: [1] });
    renderApp(<AgentAccessSettings />);
    const args = (await screen.findByLabelText("Tool arguments (JSON)")) as HTMLTextAreaElement;
    expect(JSON.parse(args.value)).toEqual({ query: "vector database" });
    await userEvent.click(screen.getByRole("button", { name: "Run tool" }));
    await waitFor(() => expect(api.callTool).toHaveBeenCalledWith("search_repositories", { query: "vector database" }));
    const result = await screen.findByTestId("tool-result");
    expect(result).toHaveTextContent("1 repositories for 'vector database'");
    expect(result).toHaveTextContent("1 evidence item");
  });

  it("rejects invalid JSON without calling the tool", async () => {
    renderApp(<AgentAccessSettings />);
    const args = await screen.findByLabelText("Tool arguments (JSON)");
    await userEvent.clear(args);
    await userEvent.type(args, "not json");
    await userEvent.click(screen.getByRole("button", { name: "Run tool" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("not valid JSON");
    expect(api.callTool).not.toHaveBeenCalled();
  });
});
