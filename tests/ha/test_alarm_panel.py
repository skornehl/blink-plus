"""Each Blink system as an alarm panel, next to its Armed switch."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_integration import (
    account as account,
)
from test_integration import (
    blink as blink,
)
from test_integration import (
    custom_integrations as custom_integrations,
)

PANEL = "alarm_control_panel.garten"


async def test_alarm_panel_arms_and_disarms(hass, account, blink):
    sync = SimpleNamespace(
        name="Garten", network_id=11, arm=True, async_arm=AsyncMock(return_value=True)
    )
    blink.sync = {"Garten": sync}
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PANEL).state == "armed_away"
    assert hass.states.get("switch.garten_armed").state == "on"

    await hass.services.async_call(
        "alarm_control_panel", "alarm_disarm", {"entity_id": PANEL}, blocking=True
    )
    sync.async_arm.assert_awaited_with(False)
    sync.arm = False
    await account.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(PANEL).state == "disarmed"

    await hass.services.async_call(
        "alarm_control_panel", "alarm_arm_away", {"entity_id": PANEL}, blocking=True
    )
    sync.async_arm.assert_awaited_with(True)
    await hass.config_entries.async_unload(account.entry_id)


async def test_unknown_arm_state(hass, account, blink):
    blink.sync = {
        "Garten": SimpleNamespace(
            name="Garten", network_id=11, arm=None, async_arm=AsyncMock()
        )
    }
    assert await hass.config_entries.async_setup(account.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PANEL).state == "unknown"
    await hass.config_entries.async_unload(account.entry_id)
