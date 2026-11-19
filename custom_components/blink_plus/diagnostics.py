"""Allowlisted diagnostics: no account identifiers, tokens, URLs or serials."""

from homeassistant.loader import async_get_integration

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = entry.runtime_data
    integration = await async_get_integration(hass, DOMAIN)
    return {
        "version": str(integration.version),
        "camera_count": len(coordinator.api.cameras),
        "scan_interval": coordinator.options["scan_interval"],
        "live_enabled": coordinator.options["live_enabled"],
        "max_live_seconds": coordinator.options["max_live_seconds"],
        "usb_clip_minutes": coordinator.options["usb_clip_minutes"],
        "blinkpy_load_reductions": coordinator.load_reductions,
        "usb_archive": {
            key: coordinator.archive.status[key]
            for key in (
                "saved_total",
                "saved_last_run",
                "last_run",
                "last_result",
                "deleted_last_run",
                "deleted_total",
            )
        }
        if coordinator.archive is not None and coordinator.archive.enabled
        else None,
        "active_streams": sum(
            r.stream is not None for r in coordinator.relays.values()
        ),
        "streams": [
            {"starts": r.starts, "last_error": r.last_error}
            for r in coordinator.relays.values()
        ],
    }
