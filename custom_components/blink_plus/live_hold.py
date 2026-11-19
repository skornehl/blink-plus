"""Keep a camera's live view open from an automation.

``blink_plus.start_live_view`` uses this. A hold is one more local client of
the camera's relay, exactly like a player: it connects to the relay's loopback
listener, reads the MPEG-TS and throws it away. While it is connected the relay
keeps the Blink session open (within the maximum live session), so a player or
``blink_plus.play_audio`` that comes later finds the camera already streaming
instead of waiting for a battery camera to wake.

One hold per camera; starting it again moves its end further out. It ends after
its time, on ``blink_plus.stop_live_view``, when the relay closes the
connection (session limit, cooldown, failure) or when the entry unloads.
"""

import asyncio
from contextlib import suppress
import logging
import math
import time

_LOGGER = logging.getLogger(__name__)

READ_CHUNK = 64 * 1024
CONNECT_TIMEOUT = 5


def live_status(relay, hold=None):
    """What an automation needs to know about a camera's live view right now."""
    if relay.live:
        state = "streaming"
    elif relay.runner is not None:
        state = "starting"
    else:
        state = "idle"
    holding = hold is not None and hold.running
    return {
        "state": state,
        "holding": holding,
        "seconds": hold.remaining if holding else 0,
        "cooldown_s": max(0, math.ceil(relay.retry_after - time.monotonic())),
        "last_error": relay.last_error,
    }


class LiveHold:
    """One local client that keeps a camera's live view open for a while."""

    def __init__(self, relay, seconds):
        self.relay = relay
        self.deadline = time.monotonic() + seconds
        self.task = None

    @property
    def remaining(self):
        return max(0, round(self.deadline - time.monotonic()))

    @property
    def running(self):
        return self.task is not None and not self.task.done()

    def extend(self, seconds):
        """Keep the hold for at least ``seconds`` from now."""
        self.deadline = max(self.deadline, time.monotonic() + seconds)

    def start(self, create_task):
        self.task = create_task(self.run())
        return self.task

    async def run(self):
        """Connect like a player and read until the time is up."""
        source = await self.relay.source()
        host, port = source.removeprefix("tcp://").rsplit(":", 1)
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)), CONNECT_TIMEOUT
        )
        try:
            while (left := self.deadline - time.monotonic()) > 0:
                try:
                    chunk = await asyncio.wait_for(
                        reader.read(READ_CHUNK), min(left, 1)
                    )
                except TimeoutError:
                    continue
                if not chunk:
                    # The relay closed it: cooldown, session limit or a failure.
                    _LOGGER.debug("Live view hold ended by the relay")
                    return
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()

    async def stop(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.task
