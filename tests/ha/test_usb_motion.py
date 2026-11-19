"""Motion from a Sync Module's USB clip list, for accounts without a Blink plan."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from test_integration import (
    account as account,
)
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)
from test_motion import later, poll, started

from custom_components.blink_plus.binary_sensor import MOTION_HOLD_SECONDS
from custom_components.blink_plus.coordinator import newest_usb_clips

MOTION = "binary_sensor.test_camera_motion"


def usb_clip(when, name="Test camera"):
    return SimpleNamespace(name=name, created_at=when)


def on_drive(blink, *items):
    blink.sync = {"Home": SimpleNamespace(_local_storage={"manifest": list(items)})}


def test_newest_usb_clip_per_camera():
    start = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    found = newest_usb_clips(
        [
            usb_clip(start - timedelta(minutes=5)),  # on the drive before startup
            usb_clip(start + timedelta(minutes=1)),
            usb_clip(start + timedelta(minutes=3)),
            usb_clip(datetime(2026, 1, 1, 10, 2)),  # naive: taken as UTC
            usb_clip(start + timedelta(minutes=2), name="Other"),
            SimpleNamespace(name="No time"),
            usb_clip("not a time"),
        ],
        since=start,
    )
    assert found == {
        "Test camera": start + timedelta(minutes=3),
        "Other": start + timedelta(minutes=2),
    }
    assert newest_usb_clips(None) == {}


async def test_usb_clips_already_on_the_drive_are_not_motion(hass, account, blink):
    coordinator = await started(hass, account, blink)
    on_drive(blink, usb_clip(coordinator.started_at - timedelta(hours=1)))
    assert await poll(hass, coordinator) == "off"
    assert "Test camera" not in coordinator.clip_times


async def test_new_usb_clip_is_motion(hass, account, blink):
    coordinator = await started(hass, account, blink)
    old = usb_clip(coordinator.started_at - timedelta(hours=1))
    on_drive(blink, old)
    assert await poll(hass, coordinator) == "off"
    new = usb_clip(coordinator.started_at + timedelta(seconds=20))
    on_drive(blink, old, new)
    assert await poll(hass, coordinator) == "on"
    # The same list again neither ends nor extends it.
    await later(hass, 30)
    assert await poll(hass, coordinator) == "on"
    await later(hass, MOTION_HOLD_SECONDS + 1)
    assert await poll(hass, coordinator) == "off"
    # Another camera's clip is not motion here.
    on_drive(
        blink,
        old,
        new,
        usb_clip(coordinator.started_at + timedelta(minutes=5), name="Other"),
    )
    assert await poll(hass, coordinator) == "off"


async def test_cloud_and_usb_clips_both_count(hass, account, blink):
    coordinator = await started(hass, account, blink)
    blink.clip_list.return_value = {
        "media": [
            {
                "device_name": "Test camera",
                "created_at": (
                    coordinator.started_at + timedelta(minutes=1)
                ).isoformat(),
            }
        ]
    }
    on_drive(blink, usb_clip(coordinator.started_at + timedelta(minutes=2)))
    assert await poll(hass, coordinator) == "on"
    assert coordinator.clip_times["Test camera"] == coordinator.started_at + timedelta(
        minutes=2
    )


async def test_sync_module_without_usb_drive(hass, account, blink):
    coordinator = await started(hass, account, blink)
    blink.sync = {
        "Home": SimpleNamespace(_local_storage={"manifest": []}),
        "Old": SimpleNamespace(),
    }
    assert await poll(hass, coordinator) == "off"
    assert coordinator.last_update_success
