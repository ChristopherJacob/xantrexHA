"""Binary sensor platform for Xantrex Freedom X."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
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
    """Set up Xantrex binary sensors from config entry."""
    coordinator: XantrexFreedomXCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([XantrexShoreConnectedBinarySensor(coordinator, entry)])


class XantrexBaseBinarySensor(
    CoordinatorEntity[XantrexFreedomXCoordinator], BinarySensorEntity
):
    """Base class for coordinator-backed Xantrex binary sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry


class XantrexShoreConnectedBinarySensor(XantrexBaseBinarySensor):
    """Candidate shore-connected indicator from runtime flags."""

    _attr_name = "Shore connected (candidate)"
    _attr_icon = "mdi:power-plug"

    def __init__(self, coordinator: XantrexFreedomXCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_candidate_shore_connected"

    @property
    def is_on(self) -> bool | None:
        """Return candidate shore-connected state.

        Current best candidate signal is runtime flag bit_3.
        Source-state values are left to diagnostic entities until mapping is
        fully validated.
        """
        bits: dict[str, Any] | None = self.coordinator.data.parsed.get("runtime_flags_bits")
        if not isinstance(bits, dict):
            return None
        value = bits.get("bit_3")
        return bool(value) if value is not None else None

    @property
    def available(self) -> bool:
        """Mark candidate binary unavailable when runtime data is stale."""
        if self.coordinator.data is None:
            return False
        return not self.coordinator.data.parsed.get("runtime_is_stale", False)
