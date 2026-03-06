"""Config flow for Xantrex Freedom X integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_NAME, DOMAIN


class XantrexFreedomXConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xantrex Freedom X."""

    VERSION = 1

    async def async_step_user(self, user_input: Mapping[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        discovered = bluetooth.async_discovered_service_info(self.hass)
        preferred_candidates = {
            service_info.address: service_info.name or service_info.address
            for service_info in discovered
            if "freedom" in (service_info.name or "").lower()
        }
        all_candidates = {
            service_info.address: service_info.name or service_info.address
            for service_info in discovered
        }
        candidates = preferred_candidates or all_candidates

        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={
                    CONF_ADDRESS: address,
                    CONF_NAME: user_input[CONF_NAME],
                },
            )

        if candidates:
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(candidates),
                    vol.Required(CONF_NAME, default="Xantrex Freedom X"): str,
                }
            )
        else:
            # Allow manual entry when bluetooth advertisements are not visible yet.
            schema = vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): str,
                    vol.Required(CONF_NAME, default="Xantrex Freedom X"): str,
                }
            )

        return self.async_show_form(step_id="user", data_schema=schema)
