# Using StarIndex from other agents (MCP)

StarIndex is a Model Context Protocol server. Claude Code, Claude Desktop or any MCP client can
search your starred library while you work, with the same read-only tools the built-in Research
workflow uses.

## Connect

**Claude Code, over HTTP** (StarIndex must be running):

```bash
claude mcp add --transport http starindex http://127.0.0.1:8765/mcp
```

**Claude Code, over stdio** (works whether or not StarIndex is running):

```powershell
claude mcp add starindex -- C:\path\to\starindex\.venv\Scripts\python.exe -m starindex.cli mcp
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "starindex": {
      "command": "C:\\path\\to\\starindex\\.venv\\Scripts\\python.exe",
      "args": ["-m", "starindex.cli", "mcp"]
    }
  }
}
```

Settings → Agent access in the app shows these with the real paths of your installation filled in.

## Tools

| Tool | Purpose |
|---|---|
| `search_repositories` | hybrid search over names, descriptions, topics and READMEs, with language/topic/license/star filters |
| `search_documents` | passages in READMEs, docs, manifests and configuration |
| `search_code` | passages in source code (repositories indexed at the deep level) |
| `get_repository_details` | metadata, README opening, list of indexed files |
| `read_indexed_file` | a line range of an indexed file |
| `get_repository_dependencies` | declared dependencies and which other starred repositories share them |
| `find_similar_repositories` | related starred repositories with the reasons |
| `compare_repositories` | side-by-side table for two to five repositories, with license observations |
| `filter_by_license` | check or filter repositories by license family or SPDX id |
| `get_repository_activity` | last push, maintenance status, archive state |
| `get_collection` | repositories in one of your collections |

Results carry the same evidence as in the app: repository, path, line range, commit and a GitHub
permalink.

## How it behaves

- **Read-only.** No tool writes, reaches the network or touches the file system. `save_research_report`
  is not exposed.
- **Two transports, one implementation.** `POST /mcp` on the running application (Streamable HTTP with
  JSON responses, stateless) and `starindex mcp` (stdio). The stdio process forwards to the running
  application when it is up. When it is not, it reads the database directly in keyword-only mode, so it
  never takes the lock on the embedded vector index; results then say so.
- **Untrusted content is labelled.** Tool output is text quoted from third-party repositories. Every
  result starts with a notice saying so, and passages that look like instructions are marked. The agent
  you connect still decides what to do with that text, so keep its own permissions conservative.
- **Local only.** The HTTP endpoint is served on the loopback address with the same Host and Origin
  checks as the rest of the API. If you start StarIndex on another address, MCP clients must send
  `Authorization: Bearer <STARINDEX_ACCESS_TOKEN>`.
- **Protocol.** Tools capability only (`initialize`, `ping`, `tools/list`, `tools/call`), protocol
  versions 2024-11-05 through 2025-11-25, written against the JSON-RPC wire format without an SDK
  dependency. Tested with the reference Python SDK client.
