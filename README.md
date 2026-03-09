# Xantrex Freedom X Home Assistant Integration (Starter)

This repository now contains a **starter custom integration** for Home Assistant aimed at the
**Xantrex Freedom X 2000W True Sine Wave Inverter** (via the Bluetooth panel).

## What this starter provides

- A Home Assistant custom component scaffold under `custom_components/xantrex_freedom_x`.
- A config flow that discovers nearby Bluetooth devices and lets you pick the inverter panel.
- A `DataUpdateCoordinator` polling loop (placeholder protocol parser).
- Basic diagnostic sensors (`connection_status`, `raw_payload`) so you can verify transport.

## What you still need to do

Because Xantrex does not publish the Bluetooth protocol publicly, this starter intentionally
uses placeholders for payload parsing.

1. Capture BLE GATT traffic from the panel (notifications + read/write operations).
2. Identify key characteristics and payload formats.
3. Implement parsing in `coordinator.py` (`_parse_payload`) and add richer sensors.
4. Optionally add controls (switch/select/number entities) once write commands are known.

## Local development workflow

1. Copy this repo into your Home Assistant config under:
   - `<config>/custom_components/xantrex_freedom_x`
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for
   **Xantrex Freedom X**.
4. Select the discovered Bluetooth device and submit.
5. Inspect created sensors and logs to continue reverse engineering.

## Fast deploy from VS Code

This repo includes a deploy script and VS Code tasks so you can push changes to Home Assistant
without manual copying each time.

1. Ensure you can SSH to your Home Assistant host.
2. In VS Code, run:
   - `Terminal -> Run Task... -> HA: Deploy integration`
   - or `HA: Deploy integration + restart`
3. Enter prompts for host, config path, user, port (and restart command if used).

The underlying script is `scripts/deploy_to_ha.sh`.

## Reverse engineering tips

- Enable debug logging for this integration and Bluetooth stack.
- Use an Android BLE sniffer app and compare values with panel readings.
- If you can access a serial/CAN equivalent protocol for the inverter, map fields against BLE.

## Current Decode Status

The integration now reads recurring runtime frames from vendor-service characteristic
`00002a03-0000-1000-8000-00805f9b34fb` (labeled "Reconnection Address" by adopted UUID naming).

### Runtime frame families observed

- `runtime_status`: changing operational telemetry, usually 20-byte payloads.
- `capability_profile`: mostly static payload with `2000` rating words.
- `unknown`: adopted/time/service metadata and transitional layouts.

### Runtime word mapping (`u16le_words`) from `runtime_status`

| Index | Typical values seen | Interpretation | Confidence |
| --- | --- | --- | --- |
| `0` | `~1183-1207` | AC voltage x10 channel A | Medium |
| `1` | `~599-600` | AC frequency x10 channel A | Medium |
| `2` | `~1183-1207` | AC voltage x10 channel B / duplicate | Medium |
| `3` | `~599-600` | AC frequency x10 channel B / duplicate | Medium |
| `4` | `~45-74` | Load-related raw value (`candidate_output_current_tenths_a`) | Low |
| `5` | `0` to `1300+` | Output power watts (`output_power_w`) | High |
| `6` | `0-4` | AC source state raw enum candidate | Medium |
| `7` | `~208-234` | Runtime flags bitfield (`runtime_flags_raw`) | Medium |
| `8` | `~1400-1450` | Runtime counter/state raw | Low |
| `9` | `0-29` | Runtime subcounter/stage raw | Low |

### Shore power candidates

- `ac_source_state_raw` appears to represent source mode but can be transitional.
- `runtime_flags_bits.bit_3` is currently the best shore-connected candidate signal.
- `Shore connected (candidate)` currently uses `runtime_flags_bits.bit_3` only.

### Current entities (work in progress)

- `AC output voltage`
- `AC output frequency`
- `Output power`
- `Output current (derived)`
- `AC source state (candidate)`
- `Runtime flags (candidate)`
- `Shore connected (candidate)` (binary sensor)
- `Shore capture helper`
- `Raw payload`

### Runtime staleness behavior

Candidate runtime entities are marked unavailable when runtime telemetry has not been refreshed
for multiple polls. Freshness metadata is exposed via raw/helper attributes:

- `runtime_last_update_at`
- `runtime_polls_since_update`
- `runtime_stale_after_polls`
- `runtime_is_stale`

## Logging snippet for `configuration.yaml`

```yaml
logger:
  default: warning
  logs:
    custom_components.xantrex_freedom_x: debug
    homeassistant.components.bluetooth: debug
```
