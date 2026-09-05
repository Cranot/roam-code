# MCP protocol compatibility

Roam delegates MCP transport and protocol negotiation to FastMCP and the Python
MCP SDK. Client configuration compatibility is separate from protocol support:
a client profile does not promise support for every protocol revision.

```sh
roam mcp --compat-profile all
```

The `protocol` section reports the installed SDK version, its supported and
preferred protocol versions, and the protocol revision covered by Roam's raw
stdio regression tests. If the SDK is absent, the section explicitly reports
that state. Upgrade dependencies deliberately; this list is read from the
installed SDK, not a hard-coded promise about the latest MCP specification.

## Verified stdio handshake

The repository lock currently selects MCP SDK 1.29.1 and FastMCP 3.4.7. Its
supported revisions are 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25.
`tests/test_mcp_stdio_protocol.py` starts the real CLI and checks that:

- An initialize request for 2025-11-25 succeeds and tools can be listed.
- An initialize request missing `protocolVersion` receives error `-32602`
  instead of hanging, and a subsequent valid initialization succeeds.
- An unsupported version receives a supported version in the initialize
  response, after which the normal initialized notification and tool listing work.

These match the [2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).
They are targeted regressions, not certification against every MCP requirement
or every transport. In particular, they do not test SSE/HTTP behavior.

## Newer protocol revisions

The [2026-07-28 revision](https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning)
introduces different discovery/versioning behavior. The locked SDK does not
advertise that revision. Roam therefore does not currently claim support for
its `server/discover` or versionless-request lifecycle. Package constraints keep
MCP SDK below 2 and FastMCP below 4 until that migration is validated: both new
major releases change APIs used by Roam. This follows the
[SDK migration guidance](https://github.com/modelcontextprotocol/python-sdk).
Run a conformance suite
against an advertised revision; a test forced to a newer unsupported revision
does not measure conformance to the older lifecycle.

## Keep installation separate from transport

Install `roam-code[mcp]` before starting a stdio session. Launch the executable
directly, rather than prefixing it with `pip install ... &&`: installation can
consume a client's startup timeout and print non-protocol bytes to stdout.

```sh
python -m pip install 'roam-code[mcp]'
roam mcp --no-auto-index
```

Roam's warnings go to stderr; stdout is reserved for JSON-RPC. Build the index
separately with `roam init`. If reporting a timeout, include the Roam, FastMCP
and MCP SDK versions, transport, requested protocol revision and the direct
launch command. Keep credentials and private source out of the report.
