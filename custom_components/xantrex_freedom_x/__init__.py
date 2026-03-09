"""Xantrex Freedom X integration."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall

from .const import DOMAIN, SERVICE_SET_CAPTURE_PHASE
from .coordinator import XantrexFreedomXCoordinator

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]
SERVICE_SET_CAPTURE_PHASE_SCHEMA = vol.Schema(
    {
        vol.Optional("entry_id"): str,
        vol.Optional("phase"): str,
        vol.Optional("note"): str,
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Xantrex Freedom X from a config entry."""
    if not hass.services.has_service(DOMAIN, SERVICE_SET_CAPTURE_PHASE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_CAPTURE_PHASE,
            lambda call: _async_handle_set_capture_phase(hass, call),
            schema=SERVICE_SET_CAPTURE_PHASE_SCHEMA,
        )

    coordinator = XantrexFreedomXCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, SERVICE_SET_CAPTURE_PHASE)
    return unload_ok


def _async_handle_set_capture_phase(hass: HomeAssistant, call: ServiceCall) -> None:
    """Set active capture phase for one or all Xantrex config entries."""
    entry_id_filter = call.data.get("entry_id")
    phase = call.data.get("phase")
    note = call.data.get("note")
    if isinstance(phase, str) and "NOTE:" in phase.upper():
        phase_text, note_text = phase.split("NOTE:", 1)
        phase = phase_text.strip()
        if not note:
            note = note_text.strip()
    coordinators: dict[str, XantrexFreedomXCoordinator] = hass.data.get(DOMAIN, {})
    for entry_id, coordinator in coordinators.items():
        if entry_id_filter and entry_id != entry_id_filter:
            continue
        coordinator.set_capture_phase(phase, note)
