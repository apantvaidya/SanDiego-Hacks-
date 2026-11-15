"""uv run python -m app.local [--port 8001] [--host 127.0.0.1]"""
import argparse
import logging

import uvicorn

from app.local.server import create_app

parser = argparse.ArgumentParser(description="Care Navigator local chat page")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8001)
args = parser.parse_args()
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
print(f"Care Navigator local chat: http://{args.host}:{args.port}")
uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")
