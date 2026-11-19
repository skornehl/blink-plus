"""Camera actions for automations: snapshot, live view hold, play_audio."""

import asyncio
import math
import time
from unittest.mock import AsyncMock, MagicMock, patch
import wave

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from test_integration import (
    account as account,
)
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)

from custom_components.blink_plus.audio import encode_file
from custom_components.blink_plus.coordinator import BlinkCoordinator

CAMERA = "camera.test_camera"
SERIAL = "TEST123"
# One ADTS AAC-LC 16 kHz mono frame header (7 bytes) plus a tiny payload.
FRAME = bytes((0xFF, 0xF1, 0x60, 0x40, 0x01, 0x3F, 0xFC)) + b"\0\0"


class FakeRelay:
    """The parts of NativeRelay the actions use, with a real loopback listener."""

    def __init__(self):
        self.runner = None
        self.stream = None
        self.retry_after = 0.0
        self.last_error = None
        self.server = None
        self.connections = 0
        self.talk_lock = asyncio.Lock()
        self.spoken = []
        self.state_listeners = set()
        self.tasks = set()

    @property
    def live(self):
        return self.stream is not None

    async def _accept(self, reader, writer):
        self.tasks.add(asyncio.current_task())
        self.connections += 1
        self.runner = MagicMock()
        try:
            while not reader.at_eof():
                writer.write(b"\x47" * 188)
                await writer.drain()
                await asyncio.sleep(0.05)
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            self.tasks.discard(asyncio.current_task())

    async def source(self):
        if self.server is None:
            self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        return f"tcp://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def start_talk(self):
        self.spoken.append("start")

    async def send_audio(self, frame):
        self.spoken.append(frame)

    async def stop_talk(self):
        self.spoken.append("stop")

    async def close(self):
        for task in tuple(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.server:
            self.server.close()
            await self.server.wait_closed()


async def started(hass, account):
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    return account.runtime_data


def with_fake_relay(coordinator):
    relay = FakeRelay()
    coordinator.relays[SERIAL] = relay
    return relay


async def call(hass, name, response=False, **data):
    return await hass.services.async_call(
        "blink_plus",
        name,
        {"entity_id": CAMERA, **data},
        blocking=True,
        return_response=response,
    )


async def test_trigger_camera_takes_a_snapshot(hass, account, blink):
    await started(hass, account)
    with patch.object(BlinkCoordinator, "snapshot", AsyncMock()) as snapshot:
        await call(hass, "trigger_camera")
    snapshot.assert_awaited_once_with(blink.cameras["Test camera"])
    await hass.config_entries.async_unload(account.entry_id)


async def test_start_live_view_holds_extends_and_stops(
    hass, account, blink, socket_enabled
):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    answer = (await call(hass, "start_live_view", True, seconds=30))[CAMERA]
    assert answer["holding"] is True
    assert answer["state"] == "starting"
    assert 29 <= answer["seconds"] <= 30
    assert relay.connections == 1
    # Again: the same hold, now longer.
    answer = (await call(hass, "start_live_view", True, seconds=100))[CAMERA]
    assert 99 <= answer["seconds"] <= 100
    assert relay.connections == 1
    runner = relay.runner
    await call(hass, "stop_live_view")
    assert SERIAL not in coordinator.holds
    runner.cancel.assert_called_once()
    await hass.config_entries.async_unload(account.entry_id)
    await relay.close()


async def test_hold_is_capped_at_the_maximum_live_session(
    hass, account, blink, socket_enabled
):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    answer = (await call(hass, "start_live_view", True, seconds=300))[CAMERA]
    assert answer["seconds"] <= coordinator.options["max_live_seconds"]
    await hass.config_entries.async_unload(account.entry_id)
    await relay.close()


async def test_camera_in_cooldown_is_not_touched(hass, account, blink):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    relay.retry_after = time.monotonic() + 30
    answer = (await call(hass, "start_live_view", True))[CAMERA]
    assert answer["holding"] is False
    assert 29 <= answer["cooldown_s"] <= 30
    assert relay.connections == 0 and relay.server is None
    await hass.config_entries.async_unload(account.entry_id)


async def test_unload_ends_the_hold(hass, account, blink, socket_enabled):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    await call(hass, "start_live_view", seconds=60)
    hold = coordinator.holds[SERIAL]
    assert await hass.config_entries.async_unload(account.entry_id)
    assert not hold.running
    await relay.close()


async def test_live_view_state_attribute_follows_the_relay(hass, account, blink):
    coordinator = await started(hass, account)
    relay = coordinator.relays[SERIAL]  # created for the listener, contacts nobody
    assert hass.states.get(CAMERA).attributes["live_view_state"] == "idle"
    relay.runner = MagicMock()
    relay._state_changed()
    assert hass.states.get(CAMERA).attributes["live_view_state"] == "starting"
    relay.stream = MagicMock()
    relay.stream.target_writer.is_closing.return_value = False
    relay._state_changed()
    assert hass.states.get(CAMERA).attributes["live_view_state"] == "streaming"
    relay.stream = None
    relay.runner = None
    relay._state_changed()
    assert hass.states.get(CAMERA).attributes["live_view_state"] == "idle"
    await hass.config_entries.async_unload(account.entry_id)


@pytest.fixture
def sound(hass, tmp_path):
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    path = tmp_path / "hello.wav"
    path.write_bytes(b"not used: encode_file is replaced")
    return path


async def test_play_audio_speaks_through_the_open_live_view(hass, account, blink, sound):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    relay.stream = object()
    with patch(
        "custom_components.blink_plus.camera.encode_file", return_value=[FRAME] * 3
    ):
        answer = (await call(hass, "play_audio", True, file_path=str(sound)))[CAMERA]
    assert relay.spoken == ["start", FRAME, FRAME, FRAME, "stop"]
    assert answer["seconds"] == pytest.approx(0.2, abs=0.05)
    await hass.config_entries.async_unload(account.entry_id)


async def test_play_audio_needs_an_open_live_view(hass, account, blink, sound):
    coordinator = await started(hass, account)
    with_fake_relay(coordinator)
    with pytest.raises(HomeAssistantError, match="live view first"):
        await call(hass, "play_audio", file_path=str(sound))
    await hass.config_entries.async_unload(account.entry_id)


async def test_play_audio_waits_for_a_starting_live_view(hass, account, blink, sound):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    relay.runner = MagicMock()

    async def comes_up():
        await asyncio.sleep(0.6)
        relay.stream = object()

    task = hass.async_create_task(comes_up())
    with patch(
        "custom_components.blink_plus.camera.encode_file", return_value=[FRAME]
    ):
        await call(hass, "play_audio", file_path=str(sound))
    await task
    assert relay.spoken == ["start", FRAME, "stop"]
    await hass.config_entries.async_unload(account.entry_id)


async def test_play_audio_refuses_files_outside_allowed_folders(
    hass, account, blink, tmp_path
):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    relay.stream = object()
    hass.config.allowlist_external_dirs = set()
    secret = tmp_path / "secret.mp3"
    secret.write_bytes(b"x")
    with pytest.raises(ServiceValidationError, match="folder"):
        await call(hass, "play_audio", file_path=str(secret))
    with pytest.raises(ServiceValidationError, match="folder"):
        await call(hass, "play_audio", file_path="relative.mp3")
    assert relay.spoken == []
    await hass.config_entries.async_unload(account.entry_id)


async def test_someone_talking_blocks_play_audio(hass, account, blink, sound):
    coordinator = await started(hass, account)
    relay = with_fake_relay(coordinator)
    relay.stream = object()
    await relay.talk_lock.acquire()
    with (
        patch("custom_components.blink_plus.camera.encode_file", return_value=[FRAME]),
        pytest.raises(HomeAssistantError, match="already talking"),
    ):
        await call(hass, "play_audio", file_path=str(sound))
    relay.talk_lock.release()
    await hass.config_entries.async_unload(account.entry_id)


def tone(path, seconds, rate=44100):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        samples = bytearray()
        for i in range(int(seconds * rate)):
            value = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
            samples += value.to_bytes(2, "little", signed=True) * 2
        out.writeframes(bytes(samples))


def test_encode_file_gives_16_khz_mono_adts(tmp_path):
    path = tmp_path / "tone.wav"
    tone(path, 1.0)
    frames = encode_file(str(path))
    assert frames
    for frame in frames:
        assert frame[0] == 0xFF and frame[1] == 0xF1  # ADTS, MPEG-4, no CRC
        assert (frame[2] >> 2) & 0x0F == 8  # 16 kHz
        assert ((frame[2] & 1) << 2 | frame[3] >> 6) == 1  # mono
    assert 0.9 <= len(frames) * 1024 / 16000 <= 1.3


def test_encode_file_stops_at_one_minute(tmp_path, monkeypatch):
    from custom_components.blink_plus import audio

    monkeypatch.setattr(audio, "MAX_AUDIO_SECONDS", 1)
    path = tmp_path / "long.wav"
    tone(path, 3.0, rate=16000)
    frames = encode_file(str(path))
    assert len(frames) * 1024 / 16000 <= 1.3


def test_encode_file_rejects_a_file_without_audio(tmp_path):
    path = tmp_path / "empty.wav"
    path.write_bytes(b"no audio here")
    with pytest.raises(Exception):  # noqa: B017 - PyAV raises its own kinds
        encode_file(str(path))
