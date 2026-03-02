from __future__ import annotations

import uvicorn

from raelyn.services.log_timestamps import install_if_needed


def main() -> None:
    install_if_needed()
    uvicorn.run("raelyn.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
