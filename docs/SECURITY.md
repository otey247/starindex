# Security and privacy

## Threat model

StarIndex is a single-user application on the user's own machine. The assets are the user's GitHub
token, model-provider credentials, private notes and the machine itself. The untrusted inputs are
everything fetched from GitHub repositories (file content, names, descriptions, topics), model output,
and web pages in the same browser that might try to reach the local server.

## Repository content is untrusted data

- **Never executed.** Indexing reads text. It does not clone with hooks, install dependencies, run
  scripts, evaluate notebooks or import modules. Manifests are parsed, not processed by package managers.
- **Archives are never extracted to disk.** Deep indexing streams the tarball and reads regular files in
  memory. Symlinks, hard links, devices, absolute paths and `..` segments are ignored, sizes are capped
  (per file, per archive, per repository, member count) and binary files and Git LFS pointers are dropped.
- **Only GitHub is contacted.** The client talks to the configured API host, the raw-content host and
  GitHub's download hosts; redirects elsewhere are refused. URLs that appear inside repositories
  (homepages, links, images) are never fetched by the backend. The web interface renders README Markdown
  sanitised, does not execute embedded HTML and does not load remote images.
- **Prompt injection.** Text from repositories reaches a model only inside delimited evidence blocks that
  the text cannot close (delimiter look-alikes and bidirectional control characters are neutralised), with
  a standing instruction that such text is data. Passages that look like instructions are flagged at index
  time and labelled in prompts and in the UI. The decisive defence is structural: the tools a model can
  request are a fixed set of read-only lookups over the local index plus "save this report locally";
  arguments are validated; repositories must exist in the library; there is no tool that reaches the
  network, the file system or a shell. A model that is fooled can at worst write a wrong answer, and
  citation checking then reports citations and repositories that do not resolve.

## Credentials

- GitHub tokens, provider API keys and ChatGPT OAuth records are stored in the operating system keyring
  (Windows Credential Manager). Without a usable keyring they go to a file readable only by the user.
- Secrets are accepted by the API and never returned by it; they are not written to the settings table,
  reports, exports or backups. Log records are filtered for token-shaped strings and credential-bearing
  query parameters (including `id_token_hint`).
- The GitHub integration only issues `GET` requests. A fine-grained read-only token is recommended and
  the UI warns when a token carries write scopes.
- Fixed-endpoint providers (OpenAI, Anthropic, Gemini) ignore a custom base URL, so a stored key can only
  be sent to its vendor.

## Network exposure

- The server binds to `127.0.0.1`. Starting it on another address requires `STARINDEX_ACCESS_TOKEN`;
  requests then need `Authorization: Bearer <token>` (or the cookie set by opening `/?token=<token>` once).
  Use a reverse proxy with TLS for anything beyond a trusted LAN.
- Requests with an unexpected `Host` header are rejected (DNS rebinding) and state-changing requests with
  a foreign `Origin` are rejected (cross-site requests from web pages).
- The UI is served with a Content-Security-Policy that allows only same-origin scripts and connections.

## Model traffic

- Local providers (loopback or private-network addresses) are used by default.
- A provider counts as *cloud* when its kind is a hosted API or its address is not local. Cloud providers
  are blocked entirely until **Privacy → Allow cloud models** is on, and each request additionally needs an
  explicit confirmation that names the provider receiving the context (unless the user turns confirmation off).
- Background work (classification, embedding) never confirms on the user's behalf, so it does not use cloud
  providers unless confirmation has been disabled.
- **Offline mode** blocks GitHub traffic and model downloads; search keeps working on what is indexed.
- Embeddings and reranking are local unless the user selects a remote embedding provider.

## Reporting

This is a personal, locally hosted tool. If you find a vulnerability, open an issue on the repository
without including secrets or private data.
