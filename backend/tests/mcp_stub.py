"""A harmless local MCP server. Set STUB_EXTRA=1 to simulate a changed server."""
import os

from fastmcp import FastMCP

mcp = FastMCP("stub")


@mcp.tool()
def part_mass(part: str) -> float:
    """Read the mass of a part."""
    return 1.0


@mcp.tool()
def flash_firmware(path: str) -> bool:
    """Flash firmware to the board."""
    return True


@mcp.tool()
def unrelated(text: str) -> str:
    """Echo some text."""
    return text


if os.path.exists(os.path.join(os.path.dirname(__file__), "stub_changed.flag")):
    @mcp.tool()
    def added_later(text: str) -> str:
        """A tool that appeared after approval."""
        return text


if __name__ == "__main__":
    mcp.run(show_banner=False)
