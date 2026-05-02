"""Event subscriber -- placeholder for v0.2 Mr Robot security_signal integration."""
import asyncio
import logging

log = logging.getLogger(__name__)

async def event_subscriber():
    """v0.1: idle. v0.2: subscribes to atlas.events for kind=security_signal."""
    while True:
        await asyncio.sleep(300)  # 5-min idle heartbeat
        log.debug('event_subscriber heartbeat (v0.1 placeholder)')
