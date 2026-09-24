"""Serve the prepared public review using only the Hub's environment file."""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def main():
    from aethel.core.env import load_env

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / ".demo" / "public-review")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    path = args.dir.resolve() / "hub.env"
    if not path.is_file():
        raise SystemExit("Prepare the public review configuration first")
    for key in ("AETHEL_ANCHOR_KEY", "AETHEL_PINATA_JWT", "AETHEL_PINATA_KEY", "AETHEL_PINATA_SECRET"):
        os.environ.pop(key, None)
    load_env(path, override=True)
    import uvicorn

    from hub.app import create_app
    from hub.config import HubConfig

    config = HubConfig()
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
