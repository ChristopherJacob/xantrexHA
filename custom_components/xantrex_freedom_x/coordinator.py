"""DataUpdateCoordinator for Xantrex Freedom X."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
import asyncio

from bleak import BleakClient
from bleak_retry_connector import establish_connection

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

CONNECT_TIMEOUT_SECONDS = 10.0
NOTIFY_WAIT_SECONDS = 5.0
READ_RETRIES = 2
FRAME_HISTORY_SIZE = 50
PHASE_CAPTURE_HISTORY_SIZE = 120
RUNTIME_STALE_MULTIPLIER = 3
DEVICE_RESOLVE_RETRIES = 4
DEVICE_RESOLVE_DELAY_SECONDS = 1.0
VENDOR_SERVICE_UUIDS = {
    "00001910-0000-1000-8000-00805f9b34fb",
    "00001911-0000-1000-8000-00805f9b34fb",
}
DEPRIORITIZE_VENDOR_CHAR_UUIDS = {
    "00002a00-0000-1000-8000-00805f9b34fb",  # Device Name
    "00002a01-0000-1000-8000-00805f9b34fb",  # Appearance
    "00002a02-0000-1000-8000-00805f9b34fb",  # Peripheral Privacy Flag
    "00002a03-0000-1000-8000-00805f9b34fb",  # Reconnection Address
    "00002a04-0000-1000-8000-00805f9b34fb",  # PPCP
    "00002a05-0000-1000-8000-00805f9b34fb",  # Service Changed
    "00002a06-0000-1000-8000-00805f9b34fb",  # Alert Level
    "00002a07-0000-1000-8000-00805f9b34fb",  # Tx Power Level
    # Standard time characteristics seen in captures; they are static/noisy.
    "00002a08-0000-1000-8000-00805f9b34fb",  # Date Time
    "00002a09-0000-1000-8000-00805f9b34fb",  # Day of Week
    "00002a0a-0000-1000-8000-00805f9b34fb",  # Day Date Time
    "00002a0b-0000-1000-8000-00805f9b34fb",  # Exact Time 100
    "00002a0c-0000-1000-8000-00805f9b34fb",  # Exact Time 256
    "00002a0d-0000-1000-8000-00805f9b34fb",  # DST Offset
    "00002a0e-0000-1000-8000-00805f9b34fb",  # Time Zone
    "00002a0f-0000-1000-8000-00805f9b34fb",  # Local Time Information
    "00002a10-0000-1000-8000-00805f9b34fb",  # Secondary Time Zone
    "00002a11-0000-1000-8000-00805f9b34fb",
    "00002a12-0000-1000-8000-00805f9b34fb",
    "00002a13-0000-1000-8000-00805f9b34fb",
    "00002a14-0000-1000-8000-00805f9b34fb",
}
RUNTIME_STATUS_SOURCE_UUIDS = {
    "00002a03-0000-1000-8000-00805f9b34fb",
}
CAPABILITY_PROFILE_SOURCE_UUIDS = {
    "00002a05-0000-1000-8000-00805f9b34fb",
}


@dataclass
class XantrexSnapshot:
    """Current inverter snapshot."""

    connected: bool
    raw_payload_hex: str
    parsed: dict[str, Any]


class XantrexFreedomXCoordinator(DataUpdateCoordinator[XantrexSnapshot]):
    """Coordinate data updates from the Xantrex Bluetooth panel."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._entry = entry
        self._address = entry.data[CONF_ADDRESS]
        self._logged_services = False
        self._discovery_mode_enabled = bool(entry.options.get("discovery_mode", True))
        self._discovery_payload_history: dict[str, bytes] = {}
        self._frame_history: list[dict[str, Any]] = []
        self._last_runtime_fields: dict[str, Any] = {}
        self._last_runtime_update_at: str | None = None
        self._runtime_polls_since_update = 0
        self._capture_phase: str | None = None
        self._phase_capture_history: list[dict[str, Any]] = []
        self._last_char_uuid: str | None = None
        self._last_char_description: str | None = None
        self._last_payload: bytes | None = None
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    async def _async_update_data(self) -> XantrexSnapshot:
        """Fetch and parse latest data from inverter.

        This method currently returns placeholder payloads so the integration can
        be installed and iterated during protocol reverse engineering.
        """
        try:
            raw_payload = await self._read_panel_payload()
            previous_payload = self._last_payload
            parsed = self._parse_payload(raw_payload, previous_payload=previous_payload)
            self._last_payload = raw_payload
            parsed["source_char_uuid"] = self._last_char_uuid
            parsed["source_char_description"] = self._last_char_description
            self._append_frame_history(raw_payload, parsed)
            self._append_phase_capture(raw_payload, parsed)
            self._merge_runtime_field_fallback(parsed)
            self._update_runtime_freshness(parsed)
            parsed["frame_history"] = list(self._frame_history)
            parsed["field_diff_summary"] = self._build_field_diff_summary()
            return XantrexSnapshot(
                connected=True,
                raw_payload_hex=raw_payload.hex(),
                parsed=parsed,
            )
        except Exception as err:  # broad by design while protocol is evolving
            if "is not available" in str(err):
                self.logger.warning(
                    "Bluetooth device %s is currently unavailable; marking data stale",
                    self._address,
                )
                parsed = self._parse_payload(self._last_payload) if self._last_payload else {}
                parsed["source_char_uuid"] = self._last_char_uuid
                parsed["source_char_description"] = self._last_char_description
                self._merge_runtime_field_fallback(parsed)
                self._update_runtime_freshness(parsed)
                parsed["frame_history"] = list(self._frame_history)
                parsed["field_diff_summary"] = self._build_field_diff_summary()
                return XantrexSnapshot(
                    connected=False,
                    raw_payload_hex=self._last_payload.hex() if self._last_payload else "",
                    parsed=parsed,
                )
            if self._is_transient_no_payload_error(err):
                self.logger.warning(
                    "No usable BLE payload yet from %s (%s); exposing disconnected state and retrying",
                    self._address,
                    err,
                )
                parsed = self._parse_payload(self._last_payload) if self._last_payload else {}
                parsed["source_char_uuid"] = self._last_char_uuid
                parsed["source_char_description"] = self._last_char_description
                self._merge_runtime_field_fallback(parsed)
                self._update_runtime_freshness(parsed)
                parsed["frame_history"] = list(self._frame_history)
                parsed["field_diff_summary"] = self._build_field_diff_summary()
                return XantrexSnapshot(
                    connected=False,
                    raw_payload_hex=self._last_payload.hex() if self._last_payload else "",
                    parsed=parsed,
                )
            if self._last_payload is not None:
                self.logger.warning(
                    "Read failed for %s (%s); using last payload from previous cycle",
                    self._address,
                    err,
                )
                parsed = self._parse_payload(self._last_payload)
                parsed["source_char_uuid"] = self._last_char_uuid
                parsed["source_char_description"] = self._last_char_description
                self._merge_runtime_field_fallback(parsed)
                self._update_runtime_freshness(parsed)
                parsed["frame_history"] = list(self._frame_history)
                parsed["field_diff_summary"] = self._build_field_diff_summary()
                return XantrexSnapshot(
                    connected=True,
                    raw_payload_hex=self._last_payload.hex(),
                    parsed=parsed,
                )
            raise UpdateFailed(f"Failed to update from {self._address}: {err}") from err

    async def _read_panel_payload(self) -> bytes:
        """Read one payload frame from the panel via BLE."""
        last_error: Exception | None = None
        for attempt in range(1, READ_RETRIES + 1):
            try:
                return await self._read_panel_payload_once()
            except Exception as err:  # broad by design while protocol is evolving
                last_error = err
                self.logger.debug(
                    "BLE read attempt %s/%s failed for %s: %s",
                    attempt,
                    READ_RETRIES,
                    self._address,
                    err,
                )
                if attempt < READ_RETRIES:
                    await asyncio.sleep(0.5)
        if last_error is not None:
            raise RuntimeError(f"No BLE payload received; last error: {last_error}") from last_error
        raise RuntimeError("No BLE payload received")

    async def _read_panel_payload_once(self) -> bytes:
        """Try notification-first, then read-only characteristic polling."""
        from homeassistant.components import bluetooth

        notification_queue: asyncio.Queue[bytes] = asyncio.Queue()
        active_notify_chars: list[Any] = []
        ble_device = await self._resolve_ble_device(bluetooth)
        if ble_device is None:
            raise RuntimeError(f"Bluetooth device {self._address} is not available")

        def _notification_callback(_: Any, data: bytearray) -> None:
            if data:
                notification_queue.put_nowait(bytes(data))

        client = await establish_connection(
            BleakClient,
            ble_device,
            self._address,
            timeout=CONNECT_TIMEOUT_SECONDS,
            ble_device_callback=lambda: self._ble_device_from_cache(bluetooth),
        )

        try:
            services = client.services
            if not self._logged_services:
                self._log_services(services)
                self._logged_services = True

            vendor_notify_chars = []
            vendor_read_chars = []
            vendor_chars_total = 0
            for service in services:
                is_vendor_service = service.uuid.lower() in VENDOR_SERVICE_UUIDS
                for char in service.characteristics:
                    props = {prop.lower() for prop in char.properties}
                    if not is_vendor_service:
                        continue
                    vendor_chars_total += 1
                    if "notify" in props:
                        vendor_notify_chars.append(char)
                    if "read" in props:
                        vendor_read_chars.append(char)
            notify_chars = vendor_notify_chars
            read_chars = vendor_read_chars

            self.logger.debug(
                "Vendor candidate chars notify=%s read=%s",
                [char.uuid for char in notify_chars],
                [char.uuid for char in read_chars],
            )

            if not notify_chars and not read_chars:
                self.logger.warning(
                    "No telemetry candidate characteristics found in vendor services. "
                    "Vendor chars seen=%s; all were adopted/static UUIDs. "
                    "Device may require pairing/auth handshake or expose telemetry over another profile.",
                    vendor_chars_total,
                )
                await self._run_discovery_scan(client, services)
                raise RuntimeError("No readable/notify characteristics found in vendor services")

            try:
                for char in notify_chars:
                    try:
                        await client.start_notify(char, _notification_callback)
                        active_notify_chars.append(char)
                    except Exception as err:  # noqa: BLE001
                        self.logger.debug(
                            "Failed to start notify for %s (%s): %s",
                            char.uuid,
                            char.description,
                            err,
                        )

                if active_notify_chars:
                    self.logger.debug(
                        "Waiting up to %.1fs for notifications from %s",
                        NOTIFY_WAIT_SECONDS,
                        [char.uuid for char in active_notify_chars],
                    )
                    payload = await asyncio.wait_for(
                        notification_queue.get(), timeout=NOTIFY_WAIT_SECONDS
                    )
                    if payload:
                        return payload
            except asyncio.TimeoutError:
                self.logger.debug("No notifications received before timeout")
            finally:
                for char in active_notify_chars:
                    try:
                        await client.stop_notify(char)
                    except Exception:  # noqa: BLE001
                        pass

            best_payload: bytes | None = None
            best_score = -1
            best_char_uuid: str | None = None
            for char in read_chars:
                try:
                    payload = await client.read_gatt_char(char)
                    if payload:
                        payload_bytes = bytes(payload)
                        payload_hex = payload_bytes.hex()
                        self.logger.debug(
                            "Read %s bytes from %s (%s): %s",
                            len(payload_bytes),
                            char.uuid,
                            char.description,
                            payload_hex,
                        )
                        self._update_runtime_cache_from_payload(payload_bytes, char.uuid)
                        score = self._payload_score(payload_bytes)
                        char_uuid_lc = char.uuid.lower()
                        if char_uuid_lc in RUNTIME_STATUS_SOURCE_UUIDS:
                            # Prefer runtime frames when present; this keeps
                            # helper/state entities populated during mixed polls.
                            score += 50
                        if char.uuid.lower() in DEPRIORITIZE_VENDOR_CHAR_UUIDS:
                            # Vendor services appear to repurpose adopted UUIDs; keep them
                            # readable, but lower priority if better candidates exist.
                            score -= 5
                        if score < 0:
                            self.logger.debug(
                                "Ignoring low-signal payload from %s (%s)",
                                char.uuid,
                                char.description,
                            )
                            continue
                        if payload_hex.startswith("aa55"):
                            self._last_char_uuid = char.uuid
                            self._last_char_description = char.description
                            return payload_bytes
                        if (
                            best_payload is None
                            or score > best_score
                            or (score == best_score and len(payload_bytes) > len(best_payload))
                        ):
                            best_payload = payload_bytes
                            best_score = score
                            best_char_uuid = char.uuid
                except Exception as err:  # noqa: BLE001
                    self.logger.debug(
                        "Failed to read %s (%s): %s",
                        char.uuid,
                        char.description,
                        err,
                    )
            if best_payload is not None:
                self._last_char_uuid = best_char_uuid
                source_char = next(
                    (char for char in read_chars if char.uuid == best_char_uuid),
                    None,
                )
                self._last_char_description = (
                    source_char.description if source_char is not None else None
                )
                return best_payload
            await self._run_discovery_scan(client, services)
        finally:
            await client.disconnect()

        raise RuntimeError("Connected but no readable/notify payload produced data")

    def _ble_device_from_cache(self, bluetooth_module: Any) -> Any:
        """Return the best currently-known BLEDevice for this address."""
        return bluetooth_module.async_ble_device_from_address(
            self.hass, self._address, True
        ) or bluetooth_module.async_ble_device_from_address(self.hass, self._address, False)

    async def _resolve_ble_device(self, bluetooth_module: Any) -> Any:
        """Resolve BLEDevice with short rediscovery retries."""
        for attempt in range(1, DEVICE_RESOLVE_RETRIES + 1):
            ble_device = self._ble_device_from_cache(bluetooth_module)
            if ble_device is not None:
                return ble_device

            self.logger.debug(
                "BLE device %s not in cache (attempt %s/%s); requesting rediscovery",
                self._address,
                attempt,
                DEVICE_RESOLVE_RETRIES,
            )
            bluetooth_module.async_rediscover_address(self.hass, self._address)
            if attempt < DEVICE_RESOLVE_RETRIES:
                await asyncio.sleep(DEVICE_RESOLVE_DELAY_SECONDS)
        return None

    def _log_services(self, services: Any) -> None:
        """Log discovered services/characteristics once for protocol mapping."""
        for service in services:
            self.logger.debug("Service %s (%s)", service.uuid, service.description)
            for char in service.characteristics:
                self.logger.debug(
                    "  Char %s (%s) props=%s",
                    char.uuid,
                    char.description,
                    ",".join(char.properties),
                )

    def _parse_payload(
        self, payload: bytes, previous_payload: bytes | None = None
    ) -> dict[str, Any]:
        """Decode payload bytes into entities.

        Update this method as you discover frame structure.
        """
        sync = payload[:2]
        declared_len = payload[2] if len(payload) > 2 else None
        body = payload[3:-1] if len(payload) > 4 else b""
        checksum = payload[-1] if payload else None
        u16le_words = [
            int.from_bytes(payload[i : i + 2], byteorder="little", signed=False)
            for i in range(0, len(payload) - 1, 2)
        ]
        frame_family = self._classify_frame_family(
            u16le_words, getattr(self, "_last_char_uuid", None)
        )
        previous_u16le_words = (
            [
                int.from_bytes(previous_payload[i : i + 2], byteorder="little", signed=False)
                for i in range(0, len(previous_payload) - 1, 2)
            ]
            if previous_payload is not None
            else None
        )
        changed_u16le_indices: list[int] = []
        if previous_u16le_words is not None:
            compare_len = min(len(u16le_words), len(previous_u16le_words))
            changed_u16le_indices = [
                index
                for index in range(compare_len)
                if u16le_words[index] != previous_u16le_words[index]
            ]

        # Common checksum candidates for quick protocol discovery.
        checksum_xor = 0
        checksum_sum = 0
        checksum_input = payload[:-1] if len(payload) > 1 else b""
        for b in checksum_input:
            checksum_xor ^= b
            checksum_sum = (checksum_sum + b) & 0xFF

        parsed = {
            "frame_len": len(payload),
            "header": sync.hex(),
            "payload": payload[2:].hex(),
            "bytes": [int(b) for b in payload],
            "starts_with_sync": sync == b"\xAA\x55",
            "declared_len_byte": declared_len,
            "body_hex": body.hex(),
            "u16le_words": u16le_words,
            "frame_family": frame_family,
            "changed_u16le_indices": changed_u16le_indices,
            "checksum_byte": checksum,
            "checksum_xor": checksum_xor if checksum is not None else None,
            "checksum_sum": checksum_sum if checksum is not None else None,
            "checksum_xor_matches": (
                checksum == checksum_xor if checksum is not None and len(payload) > 1 else None
            ),
            "checksum_sum_matches": (
                checksum == checksum_sum if checksum is not None and len(payload) > 1 else None
            ),
        }
        self._add_candidate_fields(parsed, u16le_words, frame_family)
        return parsed

    def _add_candidate_fields(
        self, parsed: dict[str, Any], u16le_words: list[int], frame_family: str
    ) -> None:
        """Attach heuristic candidate fields for fast reverse engineering."""
        if len(u16le_words) < 8:
            return
        # These are intentionally labeled as candidates until verified.
        ac_out_voltage_v = round(u16le_words[0] / 10, 1)
        ac_frequency_hz = round(u16le_words[3] / 10, 1)
        parsed["candidate_ac_out_voltage_v"] = ac_out_voltage_v
        parsed["candidate_ac_frequency_hz"] = ac_frequency_hz
        # Promoted aliases used by primary entities.
        parsed["ac_out_voltage_v"] = ac_out_voltage_v
        parsed["ac_frequency_hz"] = ac_frequency_hz
        runtime_view = self._runtime_word_view(
            u16le_words, getattr(self, "_last_char_uuid", None)
        )
        if frame_family == "runtime_status" and runtime_view is not None:
            ac_out_voltage_v = round(runtime_view["voltage_word"] / 10, 1)
            ac_frequency_hz = round(runtime_view["frequency_word"] / 10, 1)
            parsed["candidate_ac_out_voltage_v"] = ac_out_voltage_v
            parsed["candidate_ac_frequency_hz"] = ac_frequency_hz
            parsed["ac_out_voltage_v"] = ac_out_voltage_v
            parsed["ac_frequency_hz"] = ac_frequency_hz
            output_power_w = runtime_view["power_word"]
            parsed["candidate_output_power_w"] = output_power_w
            parsed["candidate_output_current_tenths_a"] = runtime_view["current_word"]
            parsed["candidate_runtime_flags_raw"] = runtime_view["flags_word"]
            parsed["candidate_ac_source_state_raw"] = runtime_view["source_word"]
            parsed["output_power_w"] = output_power_w
            parsed["ac_source_state_raw"] = runtime_view["source_word"]
            parsed["runtime_flags_raw"] = runtime_view["flags_word"]
            parsed["runtime_flags_bits"] = self._bit_flags(runtime_view["flags_word"])
            if runtime_view.get("counter_word") is not None:
                parsed["candidate_runtime_counter_raw"] = runtime_view["counter_word"]
            if runtime_view.get("subcounter_word") is not None:
                parsed["candidate_runtime_subcounter_raw"] = runtime_view["subcounter_word"]
            if ac_out_voltage_v > 0:
                parsed["output_current_a_derived"] = round(output_power_w / ac_out_voltage_v, 2)
        elif frame_family == "capability_profile":
            parsed["candidate_capability_code_raw"] = u16le_words[4]
            parsed["candidate_power_rating_w"] = u16le_words[5]
            parsed["candidate_surge_rating_w"] = u16le_words[6]
            parsed["candidate_capability_flags_raw"] = u16le_words[7]

    def _payload_score(self, payload: bytes) -> int:
        """Rank payload usefulness for reverse engineering.

        Returns a negative score for low-value frames that should be ignored.
        """
        if not payload:
            return -1
        if all(byte == 0x00 for byte in payload):
            return -1
        # Static metadata often appears as printable ASCII in adopted characteristics.
        if not payload.startswith(b"\xAA\x55") and self._is_mostly_printable_ascii(payload):
            return -1
        score = 0
        if payload.startswith(b"\xAA\x55"):
            score += 100
        non_zero = sum(1 for byte in payload if byte != 0)
        score += non_zero
        score += min(len(payload), 64)
        u16le_words = [
            int.from_bytes(payload[i : i + 2], byteorder="little", signed=False)
            for i in range(0, len(payload) - 1, 2)
        ]
        frame_family = self._classify_frame_family(
            u16le_words, getattr(self, "_last_char_uuid", None)
        )
        if frame_family == "runtime_status":
            score += 30
        elif frame_family == "capability_profile":
            score -= 10
        return score

    def _is_mostly_printable_ascii(self, payload: bytes) -> bool:
        """Return True when payload looks like text/static metadata."""
        printable = 0
        for byte in payload:
            if byte in (0x09, 0x0A, 0x0D) or 0x20 <= byte <= 0x7E:
                printable += 1
        return printable / len(payload) >= 0.8

    def _is_transient_no_payload_error(self, err: Exception) -> bool:
        """Return True for connection-success/no-telemetry conditions."""
        message = str(err)
        return (
            "No BLE payload received" in message
            or "Connected but no readable/notify payload produced data" in message
        )

    async def _run_discovery_scan(self, client: Any, services: Any) -> None:
        """Read all readable characteristics and log value changes."""
        if not self._discovery_mode_enabled:
            return

        self.logger.warning(
            "Discovery mode active: scanning all readable characteristics for %s",
            self._address,
        )
        for service in services:
            for char in service.characteristics:
                props = {prop.lower() for prop in char.properties}
                if "read" not in props:
                    continue

                key = f"{service.uuid}|{char.uuid}"
                try:
                    payload = await client.read_gatt_char(char)
                except Exception as err:  # noqa: BLE001
                    self.logger.debug(
                        "Discovery read failed %s %s (%s): %s",
                        service.uuid,
                        char.uuid,
                        char.description,
                        err,
                    )
                    continue

                payload_bytes = bytes(payload)
                if not payload_bytes:
                    continue

                previous = self._discovery_payload_history.get(key)
                self._discovery_payload_history[key] = payload_bytes

                tags = self._payload_discovery_tags(payload_bytes)
                if previous is None:
                    self.logger.debug(
                        "Discovery baseline %s %s (%s) len=%s tags=%s payload=%s",
                        service.uuid,
                        char.uuid,
                        char.description,
                        len(payload_bytes),
                        ",".join(tags),
                        payload_bytes.hex(),
                    )
                elif previous != payload_bytes:
                    self.logger.warning(
                        "Discovery changed %s %s (%s) len=%s tags=%s payload=%s prev=%s",
                        service.uuid,
                        char.uuid,
                        char.description,
                        len(payload_bytes),
                        ",".join(tags),
                        payload_bytes.hex(),
                        previous.hex(),
                    )

    def _payload_discovery_tags(self, payload: bytes) -> list[str]:
        """Tag payload shape to make discovery logs easier to triage."""
        tags: list[str] = []
        if payload.startswith(b"\xAA\x55"):
            tags.append("sync_aa55")
        if all(byte == 0x00 for byte in payload):
            tags.append("all_zero")
        if self._is_mostly_printable_ascii(payload):
            tags.append("ascii_like")
        if not tags:
            tags.append("binary")
        return tags

    def _append_frame_history(self, payload: bytes, parsed: dict[str, Any]) -> None:
        """Store a compact rolling frame buffer for offline review."""
        record = {
            "raw_payload_hex": payload.hex(),
            "source_char_uuid": self._last_char_uuid,
            "source_char_description": self._last_char_description,
            "u16le_words": parsed.get("u16le_words"),
            "frame_family": parsed.get("frame_family"),
            "changed_u16le_indices": parsed.get("changed_u16le_indices"),
        }
        self._frame_history.append(record)
        if len(self._frame_history) > FRAME_HISTORY_SIZE:
            self._frame_history = self._frame_history[-FRAME_HISTORY_SIZE:]

    def _append_phase_capture(self, payload: bytes, parsed: dict[str, Any]) -> None:
        """Store phase-tagged snapshots for shore toggle workflows."""
        if self._capture_phase is None:
            return
        if parsed.get("frame_family") != "runtime_status":
            return
        record = {
            "at": self._utc_now(),
            "phase": self._capture_phase,
            "raw_payload_hex": payload.hex(),
            "ac_source_state_raw": parsed.get("ac_source_state_raw"),
            "runtime_flags_raw": parsed.get("runtime_flags_raw"),
            "runtime_flags_bits": parsed.get("runtime_flags_bits"),
            "output_power_w": parsed.get("output_power_w"),
        }
        self._phase_capture_history.append(record)
        if len(self._phase_capture_history) > PHASE_CAPTURE_HISTORY_SIZE:
            self._phase_capture_history = self._phase_capture_history[-PHASE_CAPTURE_HISTORY_SIZE:]

    def set_capture_phase(self, phase: str | None, note: str | None = None) -> None:
        """Set/clear active capture phase and add marker records."""
        self._capture_phase = phase.strip().upper() if phase else None
        marker = {
            "at": self._utc_now(),
            "phase": self._capture_phase,
            "type": "marker",
        }
        if note:
            marker["note"] = note
        self._phase_capture_history.append(marker)
        if len(self._phase_capture_history) > PHASE_CAPTURE_HISTORY_SIZE:
            self._phase_capture_history = self._phase_capture_history[-PHASE_CAPTURE_HISTORY_SIZE:]

    def get_phase_capture_state(self) -> dict[str, Any]:
        """Return compact phase capture state for helper entities."""
        return {
            "active_phase": self._capture_phase,
            "recent_phase_captures": self._phase_capture_history[-20:],
        }

    def _build_field_diff_summary(self) -> dict[str, Any]:
        """Summarize index-level change activity over recent runtime frames."""
        runtime_records = [
            record
            for record in self._frame_history
            if record.get("frame_family") == "runtime_status" and record.get("u16le_words")
        ]
        if len(runtime_records) < 2:
            return {
                "runtime_frames_analyzed": len(runtime_records),
                "top_changed_indices": [],
                "index_stats": {},
            }

        max_words = max(len(record["u16le_words"]) for record in runtime_records)
        change_counts = [0 for _ in range(max_words)]
        mins = [None for _ in range(max_words)]
        maxs = [None for _ in range(max_words)]

        previous_words: list[int] | None = None
        for record in runtime_records:
            words = record["u16le_words"]
            for index, value in enumerate(words):
                mins[index] = value if mins[index] is None else min(mins[index], value)
                maxs[index] = value if maxs[index] is None else max(maxs[index], value)
            if previous_words is not None:
                compare_len = min(len(previous_words), len(words))
                for index in range(compare_len):
                    if words[index] != previous_words[index]:
                        change_counts[index] += 1
            previous_words = words

        index_stats: dict[str, Any] = {}
        for index in range(max_words):
            if mins[index] is None or maxs[index] is None:
                continue
            index_stats[str(index)] = {
                "changes": change_counts[index],
                "min": mins[index],
                "max": maxs[index],
                "range": maxs[index] - mins[index],
            }

        top_changed_indices = [
            index
            for index, _ in sorted(
                enumerate(change_counts),
                key=lambda item: item[1],
                reverse=True,
            )
            if change_counts[index] > 0
        ][:6]

        return {
            "runtime_frames_analyzed": len(runtime_records),
            "top_changed_indices": top_changed_indices,
            "index_stats": index_stats,
        }

    def _classify_frame_family(
        self, u16le_words: list[int], source_char_uuid: str | None
    ) -> str:
        """Classify recurring frame patterns for better prioritization."""
        source_uuid = (source_char_uuid or "").lower()
        if len(u16le_words) >= 8:
            if (
                source_uuid in CAPABILITY_PROFILE_SOURCE_UUIDS
                and u16le_words[1] == 166
                and u16le_words[5] == 2000
                and u16le_words[6] == 2000
            ):
                return "capability_profile"
            if self._runtime_word_view(u16le_words, source_uuid) is not None:
                return "runtime_status"
        return "unknown"

    def _bit_flags(self, value: int) -> dict[str, bool]:
        """Return bit flags for 16-bit status words."""
        return {f"bit_{bit}": bool(value & (1 << bit)) for bit in range(16)}

    def _runtime_word_view(
        self, u16le_words: list[int], source_char_uuid: str | None
    ) -> dict[str, int | None] | None:
        """Return normalized runtime fields across known 2a03 layouts."""
        source_uuid = (source_char_uuid or "").lower()
        if source_uuid not in RUNTIME_STATUS_SOURCE_UUIDS:
            return None
        # Primary layout: duplicated voltage/frequency channels.
        if (
            len(u16le_words) >= 10
            and u16le_words[0] == u16le_words[2]
            and abs(u16le_words[1] - u16le_words[3]) <= 1
            and 1100 <= u16le_words[0] <= 1300
            and 550 <= u16le_words[1] <= 650
        ):
            return {
                "voltage_word": u16le_words[0],
                "frequency_word": u16le_words[1],
                "current_word": u16le_words[4],
                "power_word": u16le_words[5],
                "source_word": u16le_words[6],
                "flags_word": u16le_words[7],
                "counter_word": u16le_words[8],
                "subcounter_word": u16le_words[9],
            }
        # Alternate layout seen during shore transitions with leading zero words.
        if (
            len(u16le_words) >= 8
            and u16le_words[0] == 0
            and u16le_words[1] == 0
            and 1100 <= u16le_words[2] <= 1300
            and 550 <= u16le_words[3] <= 650
        ):
            return {
                "voltage_word": u16le_words[2],
                "frequency_word": u16le_words[3],
                "current_word": u16le_words[4],
                "power_word": u16le_words[5],
                "source_word": u16le_words[6],
                "flags_word": u16le_words[7],
                "counter_word": u16le_words[8] if len(u16le_words) > 8 else None,
                "subcounter_word": u16le_words[9] if len(u16le_words) > 9 else None,
            }
        return None

    def _merge_runtime_field_fallback(self, parsed: dict[str, Any]) -> None:
        """Keep shore helper fields populated when a cycle yields non-runtime data."""
        runtime_keys = (
            "ac_source_state_raw",
            "runtime_flags_raw",
            "runtime_flags_bits",
            "output_power_w",
        )
        last_runtime_fields = getattr(self, "_last_runtime_fields", {})
        if parsed.get("frame_family") == "runtime_status" and parsed.get("ac_source_state_raw") is not None:
            self._last_runtime_fields = {key: parsed.get(key) for key in runtime_keys}
            return
        if not last_runtime_fields:
            return
        for key in runtime_keys:
            if parsed.get(key) is None:
                parsed[key] = last_runtime_fields.get(key)

    def _update_runtime_cache_from_payload(self, payload: bytes, source_char_uuid: str) -> None:
        """Update last runtime fields from any readable runtime payload."""
        u16le_words = [
            int.from_bytes(payload[i : i + 2], byteorder="little", signed=False)
            for i in range(0, len(payload) - 1, 2)
        ]
        runtime_view = self._runtime_word_view(u16le_words, source_char_uuid)
        if runtime_view is None:
            return
        self._last_runtime_fields = {
            "ac_source_state_raw": runtime_view["source_word"],
            "runtime_flags_raw": runtime_view["flags_word"],
            "runtime_flags_bits": self._bit_flags(int(runtime_view["flags_word"])),
            "output_power_w": runtime_view["power_word"],
        }
        self._last_runtime_update_at = self._utc_now()
        self._runtime_polls_since_update = 0

    def _update_runtime_freshness(self, parsed: dict[str, Any]) -> None:
        """Attach runtime freshness metadata to parsed snapshot."""
        if not hasattr(self, "_runtime_polls_since_update"):
            self._runtime_polls_since_update = 0
        if not hasattr(self, "_last_runtime_update_at"):
            self._last_runtime_update_at = None
        if parsed.get("frame_family") == "runtime_status":
            self._runtime_polls_since_update = 0
            if self._last_runtime_update_at is None:
                self._last_runtime_update_at = self._utc_now()
        else:
            self._runtime_polls_since_update += 1
        runtime_stale_after_polls = max(1, RUNTIME_STALE_MULTIPLIER)
        parsed["runtime_last_update_at"] = self._last_runtime_update_at
        parsed["runtime_polls_since_update"] = self._runtime_polls_since_update
        parsed["runtime_stale_after_polls"] = runtime_stale_after_polls
        parsed["runtime_is_stale"] = self._runtime_polls_since_update >= runtime_stale_after_polls

    def _utc_now(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.now(UTC).isoformat(timespec="seconds")
