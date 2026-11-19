"""Arm and disarm each Blink system as an alarm panel.

The same as the system's **Armed** switch, in the form Home Assistant's alarm
cards, HomeKit and automations written for the built-in Blink integration expect.
"""

from homeassistant.components.alarm_control_panel import (
    AlarmControlPanelEntity,
    AlarmControlPanelEntityFeature,
    AlarmControlPanelState,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    async_add_entities(
        BlinkAlarmPanel(coordinator, sync) for sync in coordinator.api.sync.values()
    )


class BlinkAlarmPanel(CoordinatorEntity, AlarmControlPanelEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_code_arm_required = False
    _attr_supported_features = AlarmControlPanelEntityFeature.ARM_AWAY

    def __init__(self, coordinator, sync):
        super().__init__(coordinator)
        self.sync = sync
        self._attr_unique_id = f"{coordinator.entry.unique_id}_{sync.network_id}_alarm"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"network_{sync.network_id}")},
            manufacturer="Blink",
            name=sync.name,
            model="Blink network",
        )

    @property
    def alarm_state(self):
        armed = self.sync.arm
        if armed is None:
            return None
        return AlarmControlPanelState.ARMED_AWAY if armed else AlarmControlPanelState.DISARMED

    async def async_alarm_arm_away(self, code=None):
        await self.coordinator.command(self.sync.async_arm, True)

    async def async_alarm_disarm(self, code=None):
        await self.coordinator.command(self.sync.async_arm, False)
