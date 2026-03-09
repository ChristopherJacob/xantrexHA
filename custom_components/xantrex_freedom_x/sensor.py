"""Sensor platform for Xantrex Freedom X."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import XantrexFreedomXCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Xantrex sensors from config entry."""
    coordinator: XantrexFreedomXCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            XantrexConnectionSensor(coordinator, entry),
            XantrexRawPayloadSensor(coordinator, entry),
            XantrexShoreCaptureSensor(coordinator, entry),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="ac_out_voltage_v",
                name="AC output voltage",
                unique_suffix="candidate_ac_output_voltage",
                device_class=SensorDeviceClass.VOLTAGE,
                native_unit_of_measurement=UnitOfElectricPotential.VOLT,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:sine-wave",
            ),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="ac_frequency_hz",
                name="AC output frequency",
                unique_suffix="candidate_ac_output_frequency",
                device_class=SensorDeviceClass.FREQUENCY,
                native_unit_of_measurement=UnitOfFrequency.HERTZ,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:waveform",
            ),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="output_power_w",
                name="Output power",
                unique_suffix="candidate_output_power",
                device_class=SensorDeviceClass.POWER,
                native_unit_of_measurement=UnitOfPower.WATT,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:flash",
            ),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="output_current_a_derived",
                name="Output current (derived)",
                unique_suffix="derived_output_current",
                device_class=SensorDeviceClass.CURRENT,
                native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
                state_class=SensorStateClass.MEASUREMENT,
                icon="mdi:current-ac",
            ),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="ac_source_state_raw",
                name="AC source state (candidate)",
                unique_suffix="candidate_ac_source_state",
                icon="mdi:transmission-tower",
            ),
            XantrexParsedMetricSensor(
                coordinator,
                entry,
                key="runtime_flags_raw",
                name="Runtime flags (candidate)",
                unique_suffix="candidate_runtime_flags",
                icon="mdi:flag-outline",
            ),
        ]
    )


class XantrexBaseSensor(CoordinatorEntity[XantrexFreedomXCoordinator], SensorEntity):
    """Base class for coordinator-backed Xantrex sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry


class XantrexConnectionSensor(XantrexBaseSensor):
    """Show whether coordinator read succeeded."""

    _attr_name = "Connection status"
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connection_status"

    @property
    def native_value(self) -> str:
        """Return connection status."""
        return "connected" if self.coordinator.data.connected else "disconnected"


class XantrexRawPayloadSensor(XantrexBaseSensor):
    """Expose latest payload for reverse engineering."""

    _attr_name = "Raw payload"
    _attr_icon = "mdi:code-json"

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_raw_payload"

    @property
    def native_value(self) -> str:
        """Return raw payload hex."""
        return self.coordinator.data.raw_payload_hex

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return parsed payload diagnostics."""
        return self.coordinator.data.parsed


class XantrexShoreCaptureSensor(XantrexBaseSensor):
    """Compact sensor for shore-toggle capture sessions."""

    _attr_name = "Shore capture helper"
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_shore_capture_helper"

    @property
    def native_value(self) -> Any:
        """Return source-state value used during shore testing."""
        return self.coordinator.data.parsed.get("ac_source_state_raw")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return only the key shore-test attributes."""
        parsed = self.coordinator.data.parsed
        capture_state = self.coordinator.get_phase_capture_state()
        return {
            "active_phase": capture_state.get("active_phase"),
            "ac_source_state_raw": parsed.get("ac_source_state_raw"),
            "runtime_flags_raw": parsed.get("runtime_flags_raw"),
            "runtime_flags_bits": parsed.get("runtime_flags_bits"),
            "output_power_w": parsed.get("output_power_w"),
            "runtime_last_update_at": parsed.get("runtime_last_update_at"),
            "runtime_polls_since_update": parsed.get("runtime_polls_since_update"),
            "runtime_is_stale": parsed.get("runtime_is_stale"),
            "recent_phase_captures": capture_state.get("recent_phase_captures"),
        }


class XantrexParsedMetricSensor(XantrexBaseSensor):
    """Expose candidate parsed metrics from the latest payload."""

    def __init__(
        self,
        coordinator: XantrexFreedomXCoordinator,
        entry: ConfigEntry,
        *,
        key: str,
        name: str,
        unique_suffix: str,
        icon: str,
        device_class: SensorDeviceClass | None = None,
        native_unit_of_measurement: str | None = None,
        state_class: SensorStateClass | None = None,
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = native_unit_of_measurement
        self._attr_state_class = state_class

    @property
    def native_value(self) -> Any:
        """Return parsed candidate metric value."""
        return self.coordinator.data.parsed.get(self._key)

    @property
    def available(self) -> bool:
        """Mark runtime-derived metrics unavailable when stale."""
        if self.coordinator.data is None:
            return False
        parsed = self.coordinator.data.parsed
        if parsed.get("runtime_is_stale"):
            return False
        return parsed.get(self._key) is not None
