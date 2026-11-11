/**
 * Fixture seed for `npm run dev:mock`: real, well-known repositories across the kinds of
 * things this library is for. Figures are approximate snapshots, not live data.
 */
export type Seed = [fullName: string, description: string, language: string, stars: number, license: string | null, topics: string[], categories: string[], pushedDaysAgo: number, flags?: string];

export const SEED: Seed[] = [
  // --- AI agents and orchestration
  ["langchain-ai/langgraph", "Build resilient language agents as graphs.", "Python", 21400, "MIT", ["agents", "llm", "multi-agent", "orchestration", "langchain"], ["agents"], 1],
  ["microsoft/autogen", "A programming framework for agentic AI.", "Python", 51200, "MIT", ["agents", "multi-agent", "llm", "autogen", "orchestration"], ["agents"], 3],
  ["crewAIInc/crewAI", "Framework for orchestrating role-playing, autonomous AI agents that work together on complex tasks.", "Python", 39800, "MIT", ["agents", "ai-agents", "multi-agent", "llm"], ["agents"], 2],
  ["openai/openai-agents-python", "A lightweight, powerful framework for multi-agent workflows.", "Python", 16900, "MIT", ["agents", "openai", "multi-agent", "mcp", "tracing"], ["agents"], 1],
  ["pydantic/pydantic-ai", "Agent framework and LLM library built on Pydantic, with typed tools and structured outputs.", "Python", 12800, "MIT", ["agents", "pydantic", "llm", "mcp", "structured-output"], ["agents"], 1],
  ["huggingface/smolagents", "A barebones library for agents that think in code.", "Python", 22100, "Apache-2.0", ["agents", "code-agents", "llm", "mcp"], ["agents"], 6],
  ["mastra-ai/mastra", "The TypeScript AI agent framework: workflows, agents, RAG, integrations and evals.", "TypeScript", 17600, "Apache-2.0", ["agents", "typescript", "workflows", "mcp", "rag"], ["agents"], 1],
  ["google/adk-python", "A code-first Python toolkit for building, evaluating and deploying sophisticated AI agents.", "Python", 13900, "Apache-2.0", ["agents", "gemini", "multi-agent", "mcp"], ["agents"], 2],
  ["microsoft/semantic-kernel", "Integrate cutting-edge LLM technology quickly and easily into your apps.", "C#", 26300, "MIT", ["llm", "sdk", "agents", "dotnet", "orchestration"], ["agents"], 4],
  // --- MCP servers and connectors
  ["modelcontextprotocol/servers", "Model Context Protocol servers: reference implementations and community index.", "TypeScript", 71500, "MIT", ["mcp", "model-context-protocol", "servers"], ["mcp"], 2],
  ["modelcontextprotocol/python-sdk", "The official Python SDK for Model Context Protocol servers and clients.", "Python", 19800, "MIT", ["mcp", "sdk", "python"], ["mcp"], 3],
  ["github/github-mcp-server", "GitHub's official MCP server.", "Go", 23400, "MIT", ["mcp", "github", "tools"], ["mcp"], 1],
  ["microsoft/playwright-mcp", "Playwright MCP server: browser automation through structured accessibility snapshots.", "TypeScript", 21700, "Apache-2.0", ["mcp", "playwright", "browser-automation"], ["mcp", "automation"], 2],
  ["jlowin/fastmcp", "The fast, Pythonic way to build MCP servers and clients.", "Python", 19200, "Apache-2.0", ["mcp", "python", "server", "llm"], ["mcp"], 1],
  ["upstash/context7", "Up-to-date code documentation for LLMs and AI code editors, served over MCP.", "TypeScript", 33100, "MIT", ["mcp", "documentation", "llm"], ["mcp", "devtools"], 5],
  ["wonderwhy-er/DesktopCommanderMCP", "MCP server giving Claude terminal control, file system search and diff-based file editing.", "TypeScript", 4900, "MIT", ["mcp", "terminal", "filesystem"], ["mcp", "devtools"], 9],
  // --- RAG and enterprise search
  ["qdrant/qdrant", "High-performance, massive-scale vector database and vector search engine for the next generation of AI.", "Rust", 26800, "Apache-2.0", ["vector-database", "search-engine", "embeddings", "hnsw", "rust"], ["rag"], 1],
  ["chroma-core/chroma", "Open-source search and retrieval database for AI applications.", "Rust", 23900, "Apache-2.0", ["vector-database", "embeddings", "rag", "retrieval"], ["rag"], 1],
  ["run-llama/llama_index", "LlamaIndex is the leading framework for building LLM-powered agents over your data.", "Python", 44600, "MIT", ["rag", "llm", "agents", "data-framework", "retrieval"], ["rag", "agents"], 1],
  ["infiniflow/ragflow", "Open-source RAG engine based on deep document understanding.", "Python", 66300, "Apache-2.0", ["rag", "document-understanding", "llm", "search"], ["rag"], 2],
  ["deepset-ai/haystack", "AI orchestration framework to build customizable, production-ready LLM applications with retrieval pipelines.", "Python", 22500, "Apache-2.0", ["rag", "pipelines", "retrieval", "nlp", "semantic-search"], ["rag"], 2],
  ["meilisearch/meilisearch", "A lightning-fast search engine API bringing AI-powered hybrid search to your sites and applications.", "Rust", 53800, "MIT", ["search-engine", "hybrid-search", "typo-tolerance", "rust"], ["rag"], 2],
  ["typesense/typesense", "Open-source, typo-tolerant, in-memory fuzzy search engine. An alternative to Algolia and Elasticsearch.", "C++", 24100, "GPL-3.0", ["search-engine", "typo-tolerance", "vector-search"], ["rag"], 4],
  ["lancedb/lancedb", "Developer-friendly, embedded retrieval engine for multimodal AI. Runs in-process, no server required.", "Rust", 7600, "Apache-2.0", ["vector-database", "embedded", "multimodal", "local-first"], ["rag"], 2],
  ["microsoft/graphrag", "A modular graph-based Retrieval-Augmented Generation (RAG) system.", "Python", 27900, "MIT", ["rag", "knowledge-graph", "llm"], ["rag"], 12],
  ["pgvector/pgvector", "Open-source vector similarity search for Postgres.", "C", 17400, "PostgreSQL", ["postgres", "vector-search", "embeddings"], ["rag", "data"], 14],
  ["neuml/txtai", "All-in-one open-source AI framework for semantic search, LLM orchestration and language model workflows.", "Python", 11600, "Apache-2.0", ["semantic-search", "embeddings", "rag", "local-first", "sqlite"], ["rag"], 3],
  // --- Model serving and inference
  ["ollama/ollama", "Get up and running with large language models locally.", "Go", 152000, "MIT", ["llm", "local", "inference", "gguf", "llama"], ["serving"], 1],
  ["vllm-project/vllm", "A high-throughput and memory-efficient inference and serving engine for LLMs.", "Python", 58400, "Apache-2.0", ["llm", "inference", "serving", "gpu", "openai-compatible"], ["serving"], 1],
  ["ggml-org/llama.cpp", "LLM inference in C/C++.", "C++", 85700, "MIT", ["llm", "inference", "gguf", "cpu", "quantization"], ["serving"], 1],
  ["huggingface/text-embeddings-inference", "A blazing fast inference solution for text embeddings models.", "Rust", 4100, "Apache-2.0", ["embeddings", "inference", "reranking"], ["serving"], 7],
  ["BerriAI/litellm", "Python SDK and proxy server (LLM gateway) to call 100+ LLM APIs in the OpenAI format, with cost tracking and guardrails.", "Python", 28600, "MIT", ["llm-gateway", "proxy", "openai", "cost-tracking"], ["serving", "infra"], 1],
  // --- Evaluation and observability
  ["langfuse/langfuse", "Open-source LLM engineering platform: observability, metrics, evals, prompt management, playground, datasets.", "TypeScript", 15800, "MIT", ["observability", "llm", "tracing", "evaluation", "prompt-management"], ["eval"], 1],
  ["Arize-ai/phoenix", "AI observability and evaluation.", "Python", 6900, "Elastic-2.0", ["observability", "evaluation", "tracing", "opentelemetry"], ["eval"], 1],
  ["promptfoo/promptfoo", "Test your prompts, agents and RAGs. Red teaming, pentesting and vulnerability scanning for LLMs.", "TypeScript", 8700, "MIT", ["evaluation", "red-teaming", "prompts", "testing"], ["eval", "security"], 1],
  ["explodinggradients/ragas", "Supercharge your LLM application evaluations.", "Python", 10900, "Apache-2.0", ["evaluation", "rag", "metrics"], ["eval"], 5],
  ["traceloop/openllmetry", "Open-source observability for your GenAI or LLM application, based on OpenTelemetry.", "Python", 6400, "Apache-2.0", ["observability", "opentelemetry", "llm", "governance"], ["eval"], 4],
  // --- Developer tools and coding agents
  ["openai/codex", "Lightweight coding agent that runs in your terminal.", "Rust", 47200, "Apache-2.0", ["coding-agent", "cli", "llm"], ["devtools"], 1],
  ["cline/cline", "Autonomous coding agent right in your IDE, capable of creating and editing files, executing commands and using the browser.", "TypeScript", 52300, "Apache-2.0", ["coding-agent", "vscode", "mcp"], ["devtools"], 1],
  ["Aider-AI/aider", "aider is AI pair programming in your terminal.", "Python", 37400, "Apache-2.0", ["coding-agent", "cli", "pair-programming"], ["devtools"], 6],
  ["continuedev/continue", "Ship faster with continuous AI: open-source IDE extensions and CLI for custom coding agents.", "TypeScript", 29700, "Apache-2.0", ["coding-agent", "ide", "llm"], ["devtools"], 1],
  ["astral-sh/uv", "An extremely fast Python package and project manager, written in Rust.", "Rust", 68900, "Apache-2.0", ["python", "packaging", "rust"], ["devtools"], 1],
  ["astral-sh/ruff", "An extremely fast Python linter and code formatter, written in Rust.", "Rust", 42800, "MIT", ["python", "linter", "formatter", "rust"], ["devtools"], 1],
  // --- UI components and design systems
  ["shadcn-ui/ui", "A set of beautifully designed components that you can customize, extend and build on.", "TypeScript", 98500, "MIT", ["react", "components", "tailwindcss", "radix-ui", "design-system"], ["ui"], 1],
  ["tremorlabs/tremor", "React components to build charts and dashboards. Built on Tailwind CSS and Radix UI.", "TypeScript", 16700, "Apache-2.0", ["react", "dashboard", "charts", "tailwindcss"], ["ui"], 24],
  ["magicuidesign/magicui", "UI library for design engineers. Animated components and effects you can copy and paste into your apps.", "TypeScript", 18900, "MIT", ["react", "animation", "components", "landing-page"], ["ui", "motion"], 3],
  ["radix-ui/primitives", "An open-source UI component library for building high-quality, accessible design systems and web apps.", "TypeScript", 17800, "MIT", ["react", "accessibility", "primitives", "design-system"], ["ui"], 11],
  ["recharts/recharts", "Redefined chart library built with React and D3.", "TypeScript", 25900, "MIT", ["react", "charts", "d3", "visualization"], ["ui"], 2],
  ["TanStack/table", "Headless UI for building powerful tables and datagrids for TS/JS, React, Vue, Solid, Qwik and Svelte.", "TypeScript", 27100, "MIT", ["react", "datagrid", "headless", "table"], ["ui"], 8],
  ["xyflow/xyflow", "React Flow and Svelte Flow: powerful libraries for building node-based UIs.", "TypeScript", 31800, "MIT", ["react", "node-editor", "graph", "workflow-builder"], ["ui"], 2],
  ["apache/echarts", "A powerful, interactive charting and data visualization library for the browser.", "TypeScript", 64300, "Apache-2.0", ["charts", "visualization", "canvas", "dashboard"], ["ui"], 3],
  // --- 3D rendering and simulation
  ["mrdoob/three.js", "JavaScript 3D library.", "JavaScript", 108000, "MIT", ["3d", "webgl", "webgpu", "javascript", "rendering"], ["3d"], 1],
  ["pmndrs/react-three-fiber", "A React renderer for Three.js.", "TypeScript", 29600, "MIT", ["react", "threejs", "3d", "renderer"], ["3d"], 5],
  ["pmndrs/drei", "Useful helpers for react-three-fiber.", "JavaScript", 9300, "MIT", ["react-three-fiber", "threejs", "helpers", "3d"], ["3d"], 4],
  ["BabylonJS/Babylon.js", "A powerful, beautiful, simple and open game and rendering engine packed into a friendly JavaScript framework.", "TypeScript", 24400, "Apache-2.0", ["3d", "webgl", "webgpu", "game-engine"], ["3d", "games"], 1],
  ["playcanvas/engine", "Powerful web graphics runtime built on WebGL, WebGPU, WebXR and glTF.", "JavaScript", 10700, "MIT", ["3d", "webgl", "webgpu", "gltf", "game-engine"], ["3d", "games"], 1],
  ["pmndrs/react-three-rapier", "Rapier physics in React, for react-three-fiber scenes.", "TypeScript", 1300, "MIT", ["physics", "react-three-fiber", "rapier", "3d"], ["3d"], 31],
  ["dimforge/rapier", "2D and 3D physics engines focused on performance.", "Rust", 4900, "Apache-2.0", ["physics", "simulation", "rust", "wasm"], ["3d"], 6],
  ["donmccurdy/three-pathfinding", "Navigation mesh utilities for three.js, based on PatrolJS.", "JavaScript", 1300, "MIT", ["threejs", "pathfinding", "navmesh"], ["3d", "games"], 410, "stale"],
  // --- Motion graphics and animation
  ["remotion-dev/remotion", "Make videos programmatically with React.", "TypeScript", 24800, "NOASSERTION", ["video", "react", "animation", "programmatic-video"], ["motion"], 1],
  ["3b1b/manim", "Animation engine for explanatory math videos.", "Python", 78900, "MIT", ["animation", "math", "video", "python"], ["motion"], 60],
  ["ManimCommunity/manim", "A community-maintained Python framework for creating mathematical animations.", "Python", 33200, "MIT", ["animation", "math", "video", "python"], ["motion"], 4],
  ["motiondivision/motion", "A modern animation library for React and JavaScript.", "TypeScript", 28700, "MIT", ["animation", "react", "gestures", "spring"], ["motion", "ui"], 2],
  ["greensock/GSAP", "GSAP (GreenSock Animation Platform), a JavaScript animation library for the modern web.", "JavaScript", 22600, "NOASSERTION", ["animation", "javascript", "timeline", "scrolltrigger"], ["motion"], 20],
  ["theatre-js/theatre", "Motion design editor for the web.", "TypeScript", 11900, "Apache-2.0", ["animation", "motion-design", "threejs", "editor"], ["motion", "3d"], 640, "stale"],
  ["motion-canvas/motion-canvas", "Visualize your ideas with code: a TypeScript library for creating animated videos using the Canvas API.", "TypeScript", 17700, "MIT", ["animation", "video", "canvas", "typescript"], ["motion"], 95],
  // --- Games and interactive experiences
  ["godotengine/godot", "Godot Engine: multi-platform 2D and 3D game engine.", "C++", 101000, "MIT", ["game-engine", "3d", "2d", "gamedev"], ["games"], 1],
  ["phaserjs/phaser", "A fun, free and fast 2D game framework for making HTML5 games for desktop and mobile web browsers.", "JavaScript", 38600, "MIT", ["game-framework", "2d", "html5", "webgl"], ["games"], 9],
  ["bevyengine/bevy", "A refreshingly simple data-driven game engine built in Rust.", "Rust", 41200, "Apache-2.0", ["game-engine", "ecs", "rust", "gamedev"], ["games"], 1],
  // --- Automation and productivity
  ["n8n-io/n8n", "Fair-code workflow automation platform with native AI capabilities. 400+ integrations.", "TypeScript", 142000, "NOASSERTION", ["automation", "workflow", "low-code", "integrations", "mcp"], ["automation"], 1],
  ["activepieces/activepieces", "AI agents, MCPs and AI workflow automation. An open-source replacement for Zapier.", "TypeScript", 17300, "MIT", ["automation", "workflow", "mcp", "no-code"], ["automation"], 1],
  ["browser-use/browser-use", "Make websites accessible for AI agents. Automate tasks online with ease.", "Python", 70400, "MIT", ["browser-automation", "agents", "playwright", "llm"], ["automation", "agents"], 1],
  ["microsoft/playwright", "A framework for web testing and automation across Chromium, Firefox and WebKit with a single API.", "TypeScript", 78200, "Apache-2.0", ["testing", "automation", "browser", "e2e"], ["automation", "devtools"], 1],
  // --- Cloud infrastructure (API gateways)
  ["Kong/kong", "The cloud-native API and AI gateway.", "Lua", 41900, "Apache-2.0", ["api-gateway", "api-management", "kubernetes", "ai-gateway"], ["infra"], 1],
  ["apache/apisix", "The cloud-native API gateway and AI gateway.", "Lua", 15600, "Apache-2.0", ["api-gateway", "api-management", "nginx", "kubernetes"], ["infra"], 2],
  ["TykTechnologies/tyk", "Open-source API gateway supporting REST, GraphQL, TCP and gRPC protocols.", "Go", 10300, "MPL-2.0", ["api-gateway", "api-management", "graphql", "microservices"], ["infra"], 2],
  ["envoyproxy/gateway", "Manages Envoy Proxy as a standalone or Kubernetes-based application gateway.", "Go", 2200, "Apache-2.0", ["api-gateway", "envoy", "kubernetes", "gateway-api"], ["infra"], 1],
  ["traefik/traefik", "The cloud native application proxy.", "Go", 57100, "MIT", ["reverse-proxy", "load-balancer", "kubernetes", "api-gateway"], ["infra"], 2],
  // --- Data engineering and documents
  ["docling-project/docling", "Get your documents ready for gen AI: parse PDF, DOCX, PPTX and more, locally.", "Python", 43700, "MIT", ["pdf", "document-parsing", "ocr", "rag", "local"], ["data", "rag"], 1],
  ["pymupdf/PyMuPDF", "A high-performance Python library for data extraction, analysis, conversion and manipulation of PDF documents.", "Python", 8400, "AGPL-3.0", ["pdf", "extraction", "python", "ocr"], ["data"], 3],
  ["opendatalab/MinerU", "Transforms complex documents like PDFs into LLM-ready markdown or JSON for your agentic workflows.", "Python", 46200, "AGPL-3.0", ["pdf", "document-parsing", "ocr", "markdown"], ["data"], 2],
  ["Unstructured-IO/unstructured", "Convert documents to structured data effortlessly: open-source ETL for transforming complex documents.", "Python", 12900, "Apache-2.0", ["pdf", "etl", "document-parsing", "preprocessing"], ["data"], 3],
  ["jsvine/pdfplumber", "Plumb a PDF for detailed information about each char, rectangle and line. Plus table extraction.", "Python", 8900, "MIT", ["pdf", "table-extraction", "python"], ["data"], 48],
  ["duckdb/duckdb", "An analytical in-process SQL database management system.", "C++", 33400, "MIT", ["sql", "olap", "embedded-database", "analytics"], ["data"], 1],
  ["dbt-labs/dbt-core", "dbt enables data analysts and engineers to transform their data using the same practices that software engineers use to build applications.", "Python", 11600, "Apache-2.0", ["analytics-engineering", "sql", "data-modeling", "elt"], ["data"], 2],
  // --- Security and governance
  ["guardrails-ai/guardrails", "Adding guardrails to large language models.", "Python", 5800, "Apache-2.0", ["guardrails", "llm", "validation", "governance"], ["security"], 3],
  ["NVIDIA/garak", "The LLM vulnerability scanner.", "Python", 6100, "Apache-2.0", ["security", "llm", "red-teaming", "vulnerability-scanner"], ["security"], 2],
  ["open-policy-agent/opa", "Open Policy Agent (OPA) is an open-source, general-purpose policy engine.", "Go", 10800, "Apache-2.0", ["policy", "authorization", "governance", "rego"], ["security", "infra"], 1],
  ["protectai/llm-guard", "The security toolkit for LLM interactions: sanitization, harmful language detection, prompt-injection resistance.", "Python", 2300, "MIT", ["security", "llm", "prompt-injection", "governance"], ["security"], 210, "slowing"],
  ["nomic-ai/gpt4all", "Run local LLMs on any device. Open-source and available for commercial use.", "C++", 76400, "MIT", ["llm", "local", "inference", "chatbot"], ["serving"], 280, "archived"],
];

export const CATEGORY_SEED: [slug: string, name: string, description: string, keywords: string[]][] = [
  ["agents", "AI agents and orchestration", "Frameworks for building, coordinating and running LLM agents.", ["agents", "multi-agent", "orchestration", "autonomous", "workflow"]],
  ["mcp", "MCP servers and connectors", "Model Context Protocol servers, SDKs and connectors.", ["mcp", "model-context-protocol", "connector", "tools"]],
  ["rag", "RAG and enterprise search", "Retrieval, vector databases, search engines and RAG pipelines.", ["rag", "retrieval", "vector-database", "semantic-search", "embeddings", "search-engine"]],
  ["serving", "Model serving and inference", "Runtimes and gateways that serve language and embedding models.", ["inference", "serving", "gguf", "quantization", "llm-gateway"]],
  ["eval", "AI evaluation and observability", "Tracing, evaluation, metrics and monitoring for LLM applications.", ["evaluation", "observability", "tracing", "metrics", "opentelemetry"]],
  ["data", "Data engineering", "Document parsing, ETL, analytics databases and data modelling.", ["etl", "pdf", "document-parsing", "sql", "analytics"]],
  ["devtools", "Developer tools and coding agents", "Coding agents, CLIs, linters and package managers.", ["coding-agent", "cli", "ide", "linter", "packaging"]],
  ["ui", "UI components and design systems", "Component libraries, charts, tables and design-system primitives.", ["components", "design-system", "charts", "dashboard", "react"]],
  ["3d", "3D rendering and simulation", "WebGL/WebGPU engines, renderers and physics.", ["3d", "webgl", "webgpu", "threejs", "physics", "rendering"]],
  ["motion", "Motion graphics and animation", "Animation libraries and programmatic video.", ["animation", "video", "motion-design", "timeline"]],
  ["games", "Games and interactive experiences", "Game engines and frameworks.", ["game-engine", "gamedev", "ecs", "2d"]],
  ["automation", "Automation and productivity", "Workflow automation and browser automation.", ["automation", "workflow", "no-code", "browser-automation"]],
  ["infra", "Cloud infrastructure", "API gateways, proxies and platform infrastructure.", ["api-gateway", "api-management", "kubernetes", "reverse-proxy"]],
  ["security", "Security and governance", "Guardrails, policy engines and LLM security tooling.", ["security", "governance", "guardrails", "policy", "red-teaming"]],
];

/** Concept map used by the mock's "semantic" mode so paraphrased queries find things. */
export const CONCEPTS: [pattern: RegExp, categories: string[], topics: string[]][] = [
  [/azure api management|apim|api management|gateway/i, ["infra"], ["api-gateway", "api-management", "llm-gateway"]],
  [/agentic|orchestrat|multi-agent|agents?\b/i, ["agents"], ["agents", "multi-agent", "orchestration"]],
  [/\bmcp\b|model context protocol/i, ["mcp"], ["mcp"]],
  [/dashboard|component librar|visually impressive|design system|charts?/i, ["ui"], ["dashboard", "charts", "components"]],
  [/cinematic|animation|video|motion/i, ["motion"], ["animation", "video", "programmatic-video"]],
  [/governance|observab|tracing|monitor|evaluat/i, ["eval", "security"], ["observability", "governance", "evaluation", "tracing"]],
  [/pdf|document pars|ocr/i, ["data"], ["pdf", "document-parsing", "ocr"]],
  [/semantic search|vector|embedding|without (requiring )?a? ?cloud|local-first|retriev|rag\b/i, ["rag"], ["semantic-search", "vector-database", "embeddings", "local-first", "embedded"]],
  [/sims|house builder|3d|three\.?js|render|physics|floor ?plan/i, ["3d", "games"], ["3d", "threejs", "physics", "webgl", "pathfinding"]],
  [/game|interactive/i, ["games"], ["game-engine", "gamedev"]],
  [/local (llm|model)|inference|serving|gpu/i, ["serving"], ["inference", "local", "serving"]],
  [/coding agent|pair program|ide\b/i, ["devtools"], ["coding-agent"]],
  [/automat|workflow|zapier|browser/i, ["automation"], ["automation", "workflow", "browser-automation"]],
  [/security|guardrail|prompt injection|red.?team|policy/i, ["security"], ["security", "guardrails", "prompt-injection", "policy"]],
];
