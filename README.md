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

## Logging snippet for `configuration.yaml`

```yaml
logger:
  default: warning
  logs:
    custom_components.xantrex_freedom_x: debug
    homeassistant.components.bluetooth: debug
```
