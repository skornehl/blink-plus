"""Self-contained Blink cameras, controls and sensors."""

import voluptuous as vol
from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from .const import DEFAULTS, DOMAIN, PLATFORMS
from .coordinator import BlinkCoordinator
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


async def async_setup_entry(hass, entry):
    coordinator = BlinkCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
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
