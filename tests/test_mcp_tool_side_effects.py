"""Verify every MCP @_tool wrapper has declared side-effect metadata."""

from __future__ import annotations

from roam.mcp_server import _TOOL_METADATA


class TestMcpToolSideEffectsMetadata:
    """Every @_tool wrapper must declare read_only/destructive/idempotent flags."""

    def test_all_tools_have_side_effect_metadata(self) -> None:
        """Assert every registered tool has complete side-effect declarations."""
        missing_metadata: dict[str, list[str]] = {}

        for tool_name, metadata in _TOOL_METADATA.items():
            missing_flags = []

            # Check read_only flag
            if "read_only" not in metadata:
                missing_flags.append("read_only")
            elif not isinstance(metadata["read_only"], bool):
                missing_flags.append(f"read_only (got {type(metadata['read_only']).__name__})")

            # Check destructive flag
            if "destructive" not in metadata:
                missing_flags.append("destructive")
            elif not isinstance(metadata["destructive"], bool):
                missing_flags.append(f"destructive (got {type(metadata['destructive']).__name__})")

            # Check idempotent flag
            if "idempotent" not in metadata:
                missing_flags.append("idempotent")
            elif not isinstance(metadata["idempotent"], bool):
                missing_flags.append(f"idempotent (got {type(metadata['idempotent']).__name__})")

            if missing_flags:
                missing_metadata[tool_name] = missing_flags

        # Fail with useful message if any tool is missing metadata
        assert not missing_metadata, "Tools missing side-effect metadata:\n" + "\n".join(
            f"  {name}: {', '.join(flags)}" for name, flags in sorted(missing_metadata.items())
        )
