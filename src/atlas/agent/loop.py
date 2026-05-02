"""Atlas agent main loop -- 3 concurrent coroutines under one event loop."""
import asyncio
import logging
from atlas.agent.poller import task_poller
from atlas.agent.scheduler import scheduler
from atlas.agent.event_subscriber import event_subscriber

log = logging.getLogger(__name__)

async def isolate(name, coro_factory):
    """Run coro forever with crash isolation. One crash does not cascade."""
    while True:
        try:
            await coro_factory()
        except Exception as e:
            log.exception(f'{name} crashed: {e}; restarting in 30s')
            await asyncio.sleep(30)

async def run():
    log.info('Atlas agent loop starting')
    await asyncio.gather(
        isolate('task_poller', task_poller),
        isolate('scheduler', scheduler),
        isolate('event_subscriber', event_subscriber),
    )
