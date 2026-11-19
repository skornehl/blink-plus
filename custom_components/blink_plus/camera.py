"""Native H.264/AAC cameras; cached stills never wake a camera."""

import asyncio
import logging
from pathlib import Path

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from .audio import MAX_FILE_BYTES, encode_file, speak
from .entity import BlinkEntity
from .live_hold import live_status

# play_audio waits this long for a live view that is still starting.
LIVE_WAIT_SECONDS = 20

_LOGGER = logging.getLogger(__name__)


class _NoPreload:
    """HA's per-camera stream settings with stream preloading forced off.

    Preloading keeps a stream running with no viewer, which would wake an
    on-demand cloud camera again after every session limit, indefinitely.
    """

    __slots__ = ("_settings",)

    def __init__(self, settings):
        object.__setattr__(self, "_settings", settings)

    def __getattr__(self, name):
        return False if name == "preload_stream" else getattr(self._settings, name)

    def __setattr__(self, name, value):
        if name != "preload_stream":
            setattr(self._settings, name, value)


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(
        [
            BlinkCamera(entry.runtime_data, c)
            for c in entry.runtime_data.api.cameras.values()
        ]
    )


class BlinkCamera(BlinkEntity, Camera):
    def __init__(self, coordinator, camera):
        Camera.__init__(self)
        BlinkEntity.__init__(self, coordinator, camera, "camera", None)
        if coordinator.options["live_enabled"]:
            self._attr_supported_features = CameraEntityFeature.STREAM

    @property
    def extra_state_attributes(self):
        attributes = {}
        path = self.coordinator.local_recordings.get(str(self.camera.serial))
        if path:
            attributes["last_local_recording"] = path
        relay = self.coordinator.relays.get(str(self.camera.serial))
        if self.coordinator.options["live_enabled"]:
            status = live_status(relay) if relay else None
            attributes["live_view_state"] = status["state"] if status else "idle"
            attributes["live_view_last_error"] = status["last_error"] if status else None
        return attributes or None

    @callback
    def _live_changed(self):
        if self.hass is not None:
            self.async_write_ha_state()

    async def async_trigger_camera(self):
        """blink_plus.trigger_camera: take a new snapshot."""
        await self.coordinator.snapshot(self.camera)

    def _live_relay(self):
        if not self.coordinator.options["live_enabled"]:
            raise ServiceValidationError("Live video is disabled in the Blink Plus options")
        return self.coordinator.relay(self.camera)

    async def async_start_live_view(self, seconds=60):
        """blink_plus.start_live_view: open the live view and keep it open."""
        self._live_relay()
        return await self.coordinator.start_hold(self.camera, seconds)

    async def async_stop_live_view(self):
        """blink_plus.stop_live_view: end the hold and the live view."""
        if self.coordinator.options["live_enabled"]:
            await self.coordinator.stop_hold(self.camera, end_session=True)

    async def async_play_audio(self, file_path):
        """blink_plus.play_audio: speak an audio file through the open live view."""
        relay = self._live_relay()
        path = Path(file_path)
        hass = self.hass
        if not path.is_absolute() or not await hass.async_add_executor_job(
            hass.config.is_allowed_path, str(path)
        ):
            raise ServiceValidationError(
                "The audio file must be in a folder Home Assistant allows "
                "(allowlist_external_dirs or a media folder)"
            )

        def check():
            return path.is_file() and path.stat().st_size <= MAX_FILE_BYTES

        if not await hass.async_add_executor_job(check):
            raise ServiceValidationError("The audio file is missing or larger than 8 MB")
        if relay.stream is None and relay.runner is not None:
            # Still starting (start_live_view just ran): wait for the stream.
            for _ in range(LIVE_WAIT_SECONDS * 2):
                await asyncio.sleep(0.5)
                if relay.stream is not None or relay.runner is None:
                    break
        if relay.stream is None:
            raise HomeAssistantError(
                "Open the camera's live view first (blink_plus.start_live_view)"
            )
        try:
            frames = await hass.async_add_executor_job(encode_file, str(path))
        except Exception as err:  # noqa: BLE001 - PyAV raises many kinds
            raise HomeAssistantError(
                f"The audio file could not be read ({type(err).__name__})"
            ) from None
        try:
            seconds = await speak(relay, frames)
        except RuntimeError as err:
            raise HomeAssistantError(str(err)) from None
        return {"seconds": seconds}

    async def async_camera_image(self, width=None, height=None):
        return self.camera.image_from_cache

    async def stream_source(self):
        if not self.coordinator.options["live_enabled"]:
            return None
        return await self.coordinator.relay(self.camera).source()

    async def async_create_stream(self):
        stream = await super().async_create_stream()
        if stream and not isinstance(stream.dynamic_stream_settings, _NoPreload):
            if stream.dynamic_stream_settings.preload_stream:
                _LOGGER.warning(
                    "Preload stream is ignored for %s: Blink live view starts on demand",
                    self.entity_id,
                )
            stream.dynamic_stream_settings = _NoPreload(stream.dynamic_stream_settings)
        return stream

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.coordinator.camera_entities[str(self.camera.serial)] = self
        if self.coordinator.options["live_enabled"]:
            # Creating the relay contacts nobody; it only gets a listener.
            self.coordinator.relay(self.camera).state_listeners.add(self._live_changed)

    async def async_will_remove_from_hass(self):
        self.coordinator.camera_entities.pop(str(self.camera.serial), None)
        existing = self.coordinator.relays.get(str(self.camera.serial))
        if existing:
            existing.state_listeners.discard(self._live_changed)
        await self.coordinator.stop_hold(self.camera)
        if self.stream:
            self.stream.dynamic_stream_settings.preload_stream = False
            for provider in list(self.stream.outputs().values()):
                await self.stream.remove_provider(provider)
            await self.stream.stop()
        relay = self.coordinator.relays.pop(str(self.camera.serial), None)
        if relay:
            await relay.close()
        await super().async_will_remove_from_hass()
