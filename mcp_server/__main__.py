"""python -m mcp_server [--stdio]

Default: Streamable HTTP on MCP_PORT (8765) at /mcp, bearer token required.
--stdio: serve one client over stdin/stdout (no token; for a local client that
launches the server itself, e.g. `docker compose exec -T mcp python -m mcp_server --stdio`).
"""
import logging
import os
import sys


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    from mcp_server.server import mcp, http_app
    if "--stdio" in argv:
        mcp.run(transport="stdio")
        return
    import uvicorn
    uvicorn.run(http_app(), host=os.environ.get("MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("MCP_PORT", "8765")), log_level="info")


if __name__ == "__main__":
    main()
