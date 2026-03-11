from __future__ import annotations

import uvicorn

from raelyn.config import settings
from raelyn.services.log_timestamps import install_if_needed


def main() -> None:
    install_if_needed()
    uvicorn.run("raelyn.mcp_main:app", host=settings.mcp_host, port=settings.mcp_port, reload=False)


if __name__ == "__main__":
    main()
