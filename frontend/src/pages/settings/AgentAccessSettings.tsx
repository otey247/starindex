import { useMutation, useQuery } from "@tanstack/react-query";
import { Check, Copy, Play, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/api/client";
import { errorMessage } from "@/api/errors";
import type { McpInfo, ToolInfo } from "@/api/types";
import { EvidenceList } from "@/components/evidence/EvidenceBits";
import { Button, ErrorState, Select, Skeleton, Textarea } from "@/components/ui";
import { exampleArguments } from "@/lib/feedback";
import { toast } from "@/state/stores";
import { Group, PageTitle } from "./GeneralSettings";

export function CopyBlock({ label, text, hint }: { label: string; text: string; hint?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(text).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => toast.error("Clipboard is not available; select the text and copy it."),
    );
  };
  return (
    <div className="mt-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm font-medium text-fg">{label}</p>
        <Button size="sm" variant="ghost" icon={copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />} onClick={copy} aria-label={`Copy ${label}`}>
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <pre className="mono mt-1 overflow-x-auto whitespace-pre-wrap break-all rounded-md border border-line bg-[#0a0b0c] px-3 py-2.5 text-xs leading-relaxed text-fg-2" data-testid={`copy-${label.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`}>
        {text}
      </pre>
      {hint && <p className="mt-1 text-xs leading-relaxed text-fg-3">{hint}</p>}
    </div>
  );
}

interface Param {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

/** Parameters of a tool, read from its JSON schema. */
export function toolParams(tool: ToolInfo): Param[] {
  const schema = tool.input_schema as { properties?: Record<string, Record<string, unknown>>; required?: string[] };
  const required = new Set(schema.required ?? []);
  return Object.entries(schema.properties ?? {}).map(([name, spec]) => {
    const variants = Array.isArray(spec.anyOf) ? (spec.anyOf as Record<string, unknown>[]).map((v) => v.type).filter((t) => t && t !== "null") : [];
    const type = String(spec.type ?? variants.join(" | ") ?? "any") || "any";
    return { name, type: spec.type === "array" ? "array" : type, required: required.has(name), description: String(spec.description ?? "") };
  });
}

function ToolList({ tools }: { tools: ToolInfo[] }) {
  return (
    <ul className="mt-1" data-testid="tool-list">
      {tools.map((tool) => (
        <li key={tool.name} className="border-b border-line py-2.5">
          <p className="mono text-sm font-medium text-fg">{tool.name}</p>
          <p className="mt-0.5 max-w-[80ch] text-sm leading-relaxed text-fg-3">{tool.description}</p>
          <p className="mono mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-fg-3">
            {toolParams(tool).map((p) => (
              <span key={p.name} title={p.description || undefined}>
                <span className={p.required ? "text-fg" : "text-fg-2"}>{p.name}</span>
                {p.required && (
                  <span className="text-accent" title="required" aria-label="required">
                    *
                  </span>
                )}
                <span className="text-fg-4">: {p.type}</span>
              </span>
            ))}
          </p>
        </li>
      ))}
    </ul>
  );
}

const HINTS: Record<string, unknown> = { query: "vector database", repo: "owner/name", repos: ["owner/one", "owner/two"], name: "My collection", path: "README.md" };

function ToolTester({ tools }: { tools: ToolInfo[] }) {
  const [name, setName] = useState(tools.find((t) => t.name === "search_repositories")?.name ?? tools[0]?.name ?? "");
  const tool = tools.find((t) => t.name === name);
  const [args, setArgs] = useState("");
  const [parseError, setParseError] = useState<string | null>(null);
  useEffect(() => {
    if (tool) setArgs(JSON.stringify(exampleArguments(tool.input_schema, HINTS), null, 2));
    setParseError(null);
  }, [tool?.name]); // eslint-disable-line react-hooks/exhaustive-deps
  const call = useMutation({ mutationFn: (parsed: Record<string, unknown>) => api.callTool(name, parsed) });
  const run = () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(args || "{}");
    } catch (err) {
      return setParseError(`Arguments are not valid JSON: ${(err as Error).message}`);
    }
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) return setParseError("Arguments must be a JSON object.");
    setParseError(null);
    call.mutate(parsed as Record<string, unknown>);
  };
  const result = call.data;
  return (
    <div className="mt-2 max-w-[820px]">
      <div className="flex flex-wrap items-center gap-2">
        <Select value={name} onChange={(e) => setName(e.target.value)} aria-label="Tool to try">
          {tools.map((t) => (
            <option key={t.name} value={t.name}>
              {t.name}
            </option>
          ))}
        </Select>
        <Button variant="primary" icon={<Play className="size-3.5" />} loading={call.isPending} onClick={run}>
          Run tool
        </Button>
      </div>
      <Textarea value={args} onChange={(e) => setArgs(e.target.value)} rows={5} spellCheck={false} aria-label="Tool arguments (JSON)" className="mono mt-2 text-xs" />
      {(parseError || call.isError) && (
        <p className="mt-2 flex items-start gap-2 text-sm text-fg-2" role="alert">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-danger" aria-hidden />
          {parseError ?? errorMessage(call.error)}
        </p>
      )}
      {result && (
        <div className="mt-3" data-testid="tool-result">
          <p className="label">
            {result.ok ? "Result" : "Tool reported a problem"} · {result.evidence.length} evidence item{result.evidence.length === 1 ? "" : "s"}
          </p>
          <pre className="mono mt-1.5 max-h-[280px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-line bg-[#0a0b0c] px-3 py-2.5 text-xs leading-relaxed text-fg-2">{result.summary}</pre>
          {result.evidence.length > 0 && (
            <div className="mt-3">
              <EvidenceList evidence={result.evidence} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export function AgentAccessView({ info }: { info: McpInfo }) {
  const desktop = useMemo(() => JSON.stringify(info.desktop_config, null, 2), [info.desktop_config]);
  return (
    <>
      <Group title="Connect an agent">
        {info.requires_token && (
          <p className="mt-2 flex items-start gap-2 text-sm leading-relaxed text-fg-2" data-testid="mcp-token-note">
            <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
            This StarIndex is protected by an access token. HTTP clients must send <span className="mono text-xs">Authorization: Bearer &lt;token&gt;</span> with every request.
          </p>
        )}
        <div className="max-w-[820px]">
          <CopyBlock label="Claude Code (HTTP)" text={info.claude_code_http} hint={`Talks to the running app at ${info.http_url}. Uses the same hybrid search as the UI.`} />
          <CopyBlock
            label="Claude Code (stdio)"
            text={info.claude_code_stdio}
            hint="Starts its own process, so it works even when StarIndex is not running. In that case it answers from the local database with keyword search only."
          />
          <CopyBlock label="Claude Desktop config" text={desktop} hint="Merge this into claude_desktop_config.json, then restart Claude Desktop." />
        </div>
      </Group>
      <Group title="Tools" description="All tools are read-only. Required parameters are marked with *.">
        <ToolList tools={info.tools} />
      </Group>
      <Group title="Try a tool" description="Runs the tool exactly as an agent would and shows what it gets back.">
        <ToolTester tools={info.tools} />
      </Group>
    </>
  );
}

export default function AgentAccessSettings() {
  const info = useQuery({ queryKey: ["mcp-info"], queryFn: api.mcpInfo, staleTime: 60_000 });
  return (
    <div>
      <PageTitle title="Agent access (MCP)">
        Other agents, such as Claude Code or Claude Desktop, can query this library through read-only tools over the Model Context Protocol. What a tool returns is quoted third-party text from repositories, so the receiving agent should treat it as data, not as instructions.
      </PageTitle>
      {info.isLoading ? (
        <Skeleton className="mt-6 h-64 w-full" />
      ) : info.isError || !info.data ? (
        <ErrorState className="mt-6" message={errorMessage(info.error)} onRetry={() => info.refetch()} />
      ) : (
        <AgentAccessView info={info.data} />
      )}
    </div>
  );
}
