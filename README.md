# Miner Dashboard Umbrel App

This repository packages the custom miner dashboard as an Umbrel app.

The Umbrel app is the only supported runtime for this dashboard. The legacy
standalone dashboard has been retired.

Published images use:

`ghcr.io/y5rdv2f9ys-ai/miner-dashboard:<version>`

The app uses two containers built from the same image:

- `dashboard` serves the UI and records miner history.
- `thermal` applies the configured temperature-based frequency profiles.

Both containers use host networking so they can reach miners on the LAN.
Persistent configuration, secrets, logs, and history live in the app's
`data` directory.

## Build

```bash
docker build \
  -t ghcr.io/y5rdv2f9ys-ai/miner-dashboard:1.0.0 \
  jorge-miner-dashboard/source
```

Pushing a version tag runs the included GitHub Actions workflow and publishes
the image to GHCR. The GHCR package must be public so Umbrel can pull it
without registry credentials.

## Persistent files

The app expects these files under `${APP_DATA_DIR}/data`:

- `miners_v2.json`
- `.braiins_api_token`
- `.discord_webhook_url`
- `discord_alert_config.json`
- `discord_alert_state.json`
- `active_mining_runs.json`
- `history_v2.csv`
- `miner_thermal_mode.log`
- `.page3_public_token`
- `pending_discovered_miners.json`

The entrypoint creates default miner and Discord configuration files when
they do not already exist.

## Runtime

Install and update the app through the Umbrel app-store flow. Persistent app
state remains under `${APP_DATA_DIR}/data`; do not run a second standalone
dashboard or thermal controller against the same miners.

On the Umbrel host, the installed app data lives under:

`/home/umbrel/umbrel/app-data/jorge-miner-dashboard/data`

The packaged app source in this repository lives under:

`/home/umbrel/miner-dashboard-app-store/jorge-miner-dashboard/source`

The installed app source used for local inspection lives under:

`/home/umbrel/umbrel/app-data/jorge-miner-dashboard/source`

The old standalone dashboard directory and user services were removed. Do not
recreate `/home/umbrel/miner_dashboard` or the old user units for dashboard,
thermal, or log rotation control.

## Miner discovery

The dashboard runs a one-shot LAN discovery scan at startup and exposes the
same scan from the Miners page. Discovery probes the configured subnet for
AxeOS/NerdOS miners, stores stable identity data when available, and updates a
known miner's IP only when it can match the same device. Unknown miners are
stored as pending discoveries until they are manually added.

Set `MINER_DISCOVERY_CIDR` to override the default auto-detected `/24`.

## Thermal manager

The thermal manager checks miners every 60 seconds and only writes miner
frequency. Voltage, VR temperature, reject rate, and hashrate are monitored but
do not control thermal decisions.

Per-miner thermal control uses:

- `enabled`
- `base_freq`
- `hot_freq`
- `critical_freq`
- `recover_temp`
- `warn_temp`
- `critical_temp`

Decision order:

1. At or above `critical_temp`, apply `critical_freq`.
2. Otherwise, at or above `warn_temp`, apply `hot_freq`.
3. At or below `recover_temp`, restore `base_freq`.
4. Between recovery and warning temperatures, hold the reduced frequency.

## Helpers

Keep `/home/umbrel/avalon/avalon_mode.py` manual-only unless intentionally
changing Avalon behavior. It is a helper for sending Avalon work-mode commands,
not an active service.

`GET /public/page3?token=<token>` exposes the compact page 3 status payload for
remote polling. The token is generated in `${APP_DATA_DIR}/data/.page3_public_token`
and the endpoint reuses the dashboard snapshot without extra miner polling.

Never commit tokens, webhook URLs, or files containing credentials.
