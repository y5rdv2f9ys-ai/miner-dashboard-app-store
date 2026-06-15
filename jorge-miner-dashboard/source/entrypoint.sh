#!/bin/sh
set -eu

data_dir="${MINER_DASHBOARD_DATA_DIR:-/data}"
mkdir -p "$data_dir"

for file in miners_v2.json discord_alert_config.json; do
    if [ ! -e "$data_dir/$file" ]; then
        cp "/app/defaults/$file" "$data_dir/$file"
    fi
done

if [ ! -e "$data_dir/active_mining_runs.json" ]; then
    printf '{}\n' > "$data_dir/active_mining_runs.json"
fi

touch "$data_dir/miner_thermal_mode.log"

exec "$@"
