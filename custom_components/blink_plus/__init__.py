"""Self-contained Blink cameras, controls and sensors."""

from datetime import timedelta

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from .const import DEFAULTS, DOMAIN, PLATFORMS
from .coordinator import BlinkCoordinator
from .usb_archive import MAX_CLIPS_PER_RUN, MAX_DAYS, UsbArchive

# The first archive run waits a little after Home Assistant starts.
ARCHIVE_FIRST_RUN_SECONDS = 120
from .panel import async_register_frontend, async_update_panel

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass, config):
    from .live_http import BlinkLiveView, BlinkTalkView
    from .media_source import BlinkCloudView
    from . import microphone
    from pathlib import Path
    from homeassistant.components.http import StaticPathConfig

    hass.http.register_view(BlinkCloudView())
    hass.http.register_view(BlinkLiveView())
    hass.http.register_view(BlinkTalkView())
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                "/blink_plus/frontend", str(Path(__file__).parent / "frontend"), False
            )
        ]
    )
    microphone.setup(hass)
    _register_actions(hass)
    _register_archive_actions(hass)
    # The camera card and push-to-talk element load by themselves: no dashboard Resource to add.
    await async_register_frontend(hass)
    return True


def _register_actions(hass):
    """Camera actions for automations; they act on Blink Plus camera entities."""
    for name, schema, func, response in (
        ("trigger_camera", {}, "async_trigger_camera", SupportsResponse.NONE),
        (
            "start_live_view",
            {
                vol.Optional("seconds", default=60): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=300)
                )
            },
            "async_start_live_view",
            SupportsResponse.OPTIONAL,
        ),
        ("stop_live_view", {}, "async_stop_live_view", SupportsResponse.NONE),
        (
            "play_audio",
            {vol.Required("file_path"): cv.string},
            "async_play_audio",
            SupportsResponse.OPTIONAL,
        ),
    ):
        service.async_register_platform_entity_service(
            hass,
            DOMAIN,
            name,
            entity_domain="camera",
            schema=schema,
            func=func,
            supports_response=response,
        )


def _archives(hass):
    archives = [
        entry.runtime_data.archive
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry.runtime_data.archive.enabled
    ]
    if not archives:
        raise ServiceValidationError(
            "Set an archive folder in the Blink Plus options first"
        )
    return archives


def _register_archive_actions(hass):
    async def archive_clips(call):
        results = {}
        for archive in _archives(hass):
            results[archive.coordinator.entry.title] = await archive.run(
                hours=call.data.get("hours"), max_clips=call.data["max_clips"]
            )
        return results

    async def delete_usb_clips(call):
        results = {}
        for archive in _archives(hass):
            results[archive.coordinator.entry.title] = await archive.delete_old(
                call.data["older_than_days"],
                dry_run=call.data["dry_run"],
                max_clips=call.data["max_clips"],
            )
        return results

    clips = vol.All(vol.Coerce(int), vol.Range(min=1, max=500))
    hass.services.async_register(
        DOMAIN,
        "archive_clips",
        archive_clips,
        schema=vol.Schema(
            {
                vol.Optional("hours"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=720)
                ),
                vol.Optional("max_clips", default=MAX_CLIPS_PER_RUN): clips,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        "delete_usb_clips",
        delete_usb_clips,
        schema=vol.Schema(
            {
                vol.Required("older_than_days"): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=MAX_DAYS)
                ),
                vol.Optional("dry_run", default=False): cv.boolean,
                vol.Optional("max_clips", default=MAX_CLIPS_PER_RUN): clips,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _schedule_archive(hass, entry, archive):
    minutes = entry.runtime_data.options["archive_interval"]
    if not archive.enabled or not minutes:
        return

    def run(_now=None):
        if not archive.lock.locked():
            entry.async_create_background_task(
                hass, archive.run(), "Blink Plus USB clip archive"
            )

    entry.async_on_unload(
        async_track_time_interval(hass, run, timedelta(minutes=minutes))
    )
    entry.async_on_unload(async_call_later(hass, ARCHIVE_FIRST_RUN_SECONDS, run))


async def async_setup_entry(hass, entry):
    coordinator = BlinkCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        coordinator.archive = UsbArchive(
            hass, coordinator, entry.entry_id, coordinator.options
        )
        await coordinator.archive.load()
        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await coordinator.close()
        raise

    async def shutdown(event):
        await coordinator.close()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    _schedule_archive(hass, entry, coordinator.archive)
    await async_update_panel(hass)
    return True


async def _reload(hass, entry):
    # Saved token refreshes also update the entry; only option changes need a reload.
    if {**DEFAULTS, **entry.options} != entry.runtime_data.options:
        await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.close()
        await async_update_panel(hass, skip=entry)
        return True
    return False
