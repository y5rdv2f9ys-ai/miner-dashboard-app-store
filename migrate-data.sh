#!/bin/sh
set -eu

source_dir="${1:-/home/umbrel/miner_dashboard}"
target_dir="${2:-/home/umbrel/umbrel/app-data/jorge-miner-dashboard/data}"
thermal_log="${3:-/home/umbrel/umbrel/home/Downloads/miner_thermal_mode.log}"

mkdir -p "$target_dir"

for file in \
    miners_v2.json \
    .braiins_api_token \
    .discord_webhook_url \
    discord_alert_config.json \
    discord_alert_state.json \
    active_mining_runs.json \
    history_v2.csv \
    manual_reset_marker
do
    if [ -e "$source_dir/$file" ]; then
        cp -p "$source_dir/$file" "$target_dir/$file"
    fi
done

if [ -e "$thermal_log" ]; then
    cp -p "$thermal_log" "$target_dir/miner_thermal_mode.log"
fi

chmod 600 \
    "$target_dir/.braiins_api_token" \
    "$target_dir/.discord_webhook_url" \
    "$target_dir/discord_alert_state.json" \
    2>/dev/null || true

echo "Migrated miner dashboard data to $target_dir"
