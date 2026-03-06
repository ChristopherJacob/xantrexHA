"""Tests for Xantrex Freedom X coordinator."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.xantrex_freedom_x.coordinator import XantrexFreedomXCoordinator
from homeassistant.helpers.update_coordinator import UpdateFailed


def test_parse_payload() -> None:
    """Payload parser should expose basic frame and checksum hints."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._last_payload = None
    parsed = XantrexFreedomXCoordinator._parse_payload(
        coordinator, bytes.fromhex("aa55010203")
    )

    assert parsed == {
        "frame_len": 5,
        "header": "aa55",
        "payload": "010203",
        "bytes": [170, 85, 1, 2, 3],
        "starts_with_sync": True,
        "declared_len_byte": 1,
        "body_hex": "02",
        "u16le_words": [21930, 513],
        "frame_family": "unknown",
        "changed_u16le_indices": [],
        "checksum_byte": 3,
        "checksum_xor": 252,
        "checksum_sum": 2,
        "checksum_xor_matches": False,
        "checksum_sum_matches": False,
    }


def test_async_update_data_success() -> None:
    """Coordinator should return a snapshot when read succeeds."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._address = "AA:BB:CC:DD:EE:FF"
    coordinator._last_payload = None
    coordinator._frame_history = []
    coordinator._last_char_uuid = "00002a14-0000-1000-8000-00805f9b34fb"
    coordinator._last_char_description = "Reference Time Information"
    coordinator._read_panel_payload = AsyncMock(return_value=bytes.fromhex("aa5501"))
    coordinator._parse_payload = Mock(return_value={"frame_len": 3})

    snapshot = asyncio.run(XantrexFreedomXCoordinator._async_update_data(coordinator))

    assert snapshot.connected is True
    assert snapshot.raw_payload_hex == "aa5501"
    assert snapshot.parsed == {
        "frame_len": 3,
        "source_char_uuid": "00002a14-0000-1000-8000-00805f9b34fb",
        "source_char_description": "Reference Time Information",
        "frame_history": [
            {
                "raw_payload_hex": "aa5501",
                "source_char_uuid": "00002a14-0000-1000-8000-00805f9b34fb",
                "source_char_description": "Reference Time Information",
                "u16le_words": None,
                "frame_family": None,
                "changed_u16le_indices": None,
            }
        ],
        "field_diff_summary": {
            "runtime_frames_analyzed": 0,
            "top_changed_indices": [],
            "index_stats": {},
        },
    }
    coordinator._parse_payload.assert_called_once_with(
        bytes.fromhex("aa5501"), previous_payload=None
    )


def test_async_update_data_failure_wraps_exception() -> None:
    """Coordinator should surface read errors as UpdateFailed."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._address = "11:22:33:44:55:66"
    coordinator._last_payload = None
    coordinator._read_panel_payload = AsyncMock(side_effect=RuntimeError("read failed"))

    with pytest.raises(UpdateFailed, match="11:22:33:44:55:66"):
        asyncio.run(XantrexFreedomXCoordinator._async_update_data(coordinator))


def test_async_update_data_unavailable_returns_disconnected() -> None:
    """Coordinator should return disconnected snapshot when device is unavailable."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._address = "22:33:44:55:66:77"
    coordinator._last_payload = None
    coordinator._frame_history = []
    coordinator._last_char_uuid = None
    coordinator._last_char_description = None
    coordinator._read_panel_payload = AsyncMock(
        side_effect=RuntimeError("Bluetooth device 22:33:44:55:66:77 is not available")
    )
    coordinator.logger = Mock()

    snapshot = asyncio.run(XantrexFreedomXCoordinator._async_update_data(coordinator))

    assert snapshot.connected is False
    assert snapshot.raw_payload_hex == ""
    assert snapshot.parsed == {
        "source_char_uuid": None,
        "source_char_description": None,
        "frame_history": [],
        "field_diff_summary": {
            "runtime_frames_analyzed": 0,
            "top_changed_indices": [],
            "index_stats": {},
        },
    }


def test_async_update_data_no_payload_returns_disconnected() -> None:
    """Coordinator should not fail startup on no-payload reads."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._address = "64:1C:10:31:8F:5D"
    coordinator._last_payload = None
    coordinator._frame_history = []
    coordinator._last_char_uuid = None
    coordinator._last_char_description = None
    coordinator._read_panel_payload = AsyncMock(
        side_effect=RuntimeError(
            "No BLE payload received; last error: Connected but no readable/notify payload produced data"
        )
    )
    coordinator.logger = Mock()

    snapshot = asyncio.run(XantrexFreedomXCoordinator._async_update_data(coordinator))

    assert snapshot.connected is False
    assert snapshot.raw_payload_hex == ""
    assert snapshot.parsed == {
        "source_char_uuid": None,
        "source_char_description": None,
        "frame_history": [],
        "field_diff_summary": {
            "runtime_frames_analyzed": 0,
            "top_changed_indices": [],
            "index_stats": {},
        },
    }


def test_payload_score_rejects_all_zero_payload() -> None:
    """All-zero frames should be ignored as low signal."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("00000000000000000000")
    )

    assert score < 0


def test_payload_score_prefers_sync_pattern() -> None:
    """AA55-framed payloads should rank higher than random bytes."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    sync_score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("aa55010203")
    )
    plain_score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("0102030405")
    )

    assert sync_score > plain_score


def test_payload_score_prefers_runtime_over_capability_profile() -> None:
    """Dynamic runtime frames should outrank static capability profiles."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    coordinator._last_char_uuid = "00002a03-0000-1000-8000-00805f9b34fb"
    runtime_score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("b4045702b40457023f001f000000e700a3050400")
    )
    coordinator._last_char_uuid = "00002a05-0000-1000-8000-00805f9b34fb"
    capability_score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("b004a600b0045802a600d007d007900000000000")
    )

    assert runtime_score > capability_score


def test_payload_score_rejects_mostly_ascii_without_sync() -> None:
    """ASCII-like metadata should be ignored unless framed as telemetry."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    score = XantrexFreedomXCoordinator._payload_score(
        coordinator, bytes.fromhex("393236313232303137303136393220202020202020")
    )

    assert score < 0


def test_payload_discovery_tags_identifies_sync_payload() -> None:
    """Discovery tags should mark AA55-framed payloads."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    tags = XantrexFreedomXCoordinator._payload_discovery_tags(
        coordinator, bytes.fromhex("aa550102")
    )

    assert "sync_aa55" in tags


def test_payload_discovery_tags_identifies_ascii_payload() -> None:
    """Discovery tags should mark printable ASCII payloads."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)

    tags = XantrexFreedomXCoordinator._payload_discovery_tags(
        coordinator, b"FW-1.0.0            "
    )

    assert "ascii_like" in tags


def test_parse_payload_tracks_changed_word_indices() -> None:
    """Parser should report changed u16 word positions versus prior payload."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._last_payload = None

    parsed = XantrexFreedomXCoordinator._parse_payload(
        coordinator,
        bytes.fromhex("b4045702b4045702350000000000e10086050000"),
        previous_payload=bytes.fromhex("b8045702b8045702310000000000e10086050000"),
    )

    assert parsed["changed_u16le_indices"] == [0, 2, 4]


def test_parse_payload_time_like_frame_not_runtime() -> None:
    """Time-like frame patterns should not be classified as runtime status."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._last_char_uuid = "00002a0a-0000-1000-8000-00805f9b34fb"

    parsed = XantrexFreedomXCoordinator._parse_payload(
        coordinator, bytes.fromhex("b004580250006900870092003f01000000000000")
    )

    assert parsed["frame_family"] == "unknown"


def test_build_field_diff_summary_tracks_runtime_indices() -> None:
    """Field diff summary should count changes and ranges by index."""
    coordinator = object.__new__(XantrexFreedomXCoordinator)
    coordinator._frame_history = [
        {
            "frame_family": "runtime_status",
            "u16le_words": [1200, 600, 1200, 600, 40, 0, 0, 225],
        },
        {
            "frame_family": "runtime_status",
            "u16le_words": [1201, 600, 1201, 600, 55, 30, 0, 226],
        },
        {
            "frame_family": "runtime_status",
            "u16le_words": [1201, 599, 1201, 599, 55, 32, 3, 226],
        },
    ]

    summary = XantrexFreedomXCoordinator._build_field_diff_summary(coordinator)

    assert summary["runtime_frames_analyzed"] == 3
    assert 4 in summary["top_changed_indices"]
    assert summary["index_stats"]["4"]["min"] == 40
    assert summary["index_stats"]["4"]["max"] == 55
