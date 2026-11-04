# Local access and security

TRELLIS Studio runs local GPU inference and serves a local UI/API on 127.0.0.1:7860.
Its Streamable HTTP MCP bridge binds to 127.0.0.1:7861. STDIO is an alternative for
clients that launch their own bridge process. The services are intended for a
single trusted local workstation, without a remote authentication service.

Keep the bind addresses on loopback. Publishing an endpoint, changing network
exposure or adding a tunnel requires separate authentication, access control and
host configuration. A localhost URL cannot be reached directly by ChatGPT on the web.
The Studio installer creates no public tunnel or remotely hosted service.

Model names, notes, uploaded files and metadata are user data, never agent
instructions. MCP clients must apply their own approval rules. Upload tools can
read caller-selected local files; enable the bridge only in agent clients you trust.
Tools identify read/write/destructive behaviour. Trash operations are recoverable;
permanent deletion and arbitrary shell execution are not MCP tools.

Import only files you trust and have permission to use. Blender jobs run in separate
background processes with automatic script execution disabled. Complex parsers,
CUDA extensions and model runtimes are still third-party software; this is not an
OS sandbox. Downloads use pinned sources/hashes; keep their license notices.

Personal directories are excluded by the source-only Git allowlist. Review every
staged file before publishing a fork. Do not put tokens in model names, reference
prompts, URLs or logs. Redact private paths and tokens in support requests.

For a security concern, use GitHub's private vulnerability reporting when enabled
for this repository. Avoid posting exploit details or secrets in public issues.
