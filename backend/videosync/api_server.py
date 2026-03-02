from __future__ import annotations

import uvicorn

from videosync.services.log_timestamps import install_if_needed


def main() -> None:
    install_if_needed()
    uvicorn.run("videosync.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
