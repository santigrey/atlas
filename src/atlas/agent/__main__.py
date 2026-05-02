"""Atlas Operations Agent entry point. Run via `python -m atlas.agent` or atlas-agent.service."""
import asyncio
import logging
import sys
from atlas.agent.loop import run

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    stream=sys.stderr,
)

if __name__ == '__main__':
    asyncio.run(run())
