# Miner Dashboard Umbrel App

This repository packages the custom miner dashboard as an Umbrel app.

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

The entrypoint creates default miner and Discord configuration files when
they do not already exist.

## Migrate the current installation

After Umbrel creates the app data directory, stop the old services and copy
the current state:

```bash
systemctl --user stop miner-dashboard.service miner-thermal.service
./migrate-data.sh
```

Then start the app from Umbrel. Keep the old services disabled after the app
has been verified to prevent duplicate polling and thermal control.
