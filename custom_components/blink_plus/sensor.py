"""Only expose real readings reported by each camera model."""

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfTemperature,
    UnitOfElectricPotential,
)
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util
from .const import DOMAIN
from .entity import BlinkEntity
from .values import sensor_value

SENSORS = {
    "temperature": (
        "Temperature",
        SensorDeviceClass.TEMPERATURE,
        UnitOfTemperature.FAHRENHEIT,
    ),
    "battery_voltage": (
        "Battery voltage",
        SensorDeviceClass.VOLTAGE,
        UnitOfElectricPotential.VOLT,
    ),
    "battery_level": ("Battery level (Blink bars)", None, None),
    "wifi_strength": ("Wi-Fi signal", SensorDeviceClass.SIGNAL_STRENGTH, "dBm"),
    "sync_signal_strength": ("Sync signal (Blink bars)", None, None),
    "last_record": ("Last recording", SensorDeviceClass.TIMESTAMP, None),
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = entry.runtime_data
    known = set()

    def discover():
        entities = []
        for camera in coordinator.api.cameras.values():
            for key in SENSORS:
                identity = (camera.serial, key)
                if identity not in known and sensor_value(camera, key) is not None:
                    known.add(identity)
                    entities.append(BlinkSensor(coordinator, camera, key))
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))
    if coordinator.archive is not None and coordinator.archive.enabled:
        async_add_entities([BlinkArchiveSensor(entry, coordinator.archive)])


class BlinkSensor(BlinkEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, camera, key):
        name, device_class, unit = SENSORS[key]
        super().__init__(coordinator, camera, key, name)
        self.key = key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if device_class and device_class != SensorDeviceClass.TIMESTAMP:
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return sensor_value(self.camera, self.key)


class BlinkArchiveSensor(SensorEntity):
    """When the USB clip archive last saved a clip, with what it did."""

    _attr_has_entity_name = True
    _attr_name = "Last archived clip"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:archive-arrow-down"
    _attr_should_poll = False

    def __init__(self, entry, archive):
        self.archive = archive
        self._attr_unique_id = f"{entry.entry_id}_usb_archive"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_archive")},
            name="Blink Plus clip archive",
            manufacturer="Blink Plus",
        )

    async def async_added_to_hass(self):
        self.archive.listeners.add(self.async_write_ha_state)
        self.async_on_remove(
            lambda: self.archive.listeners.discard(self.async_write_ha_state)
        )

    @property
    def native_value(self):
        when = self.archive.status["last_saved_at"]
        return dt_util.parse_datetime(when) if when else None

    @property
    def extra_state_attributes(self):
        return dict(self.archive.status)
