def get_hashrate_th(data):
    value = data.get("hashRate") or data.get("hashrate") or data.get("hash_rate") or 0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return value / 1000.0 if value > 100 else value


def get_reject_pct(data):
    accepted = data.get("sharesAccepted") or data.get("accepted") or data.get("validShares") or 0
    rejected = data.get("sharesRejected") or data.get("rejected") or data.get("invalidShares") or 0
    try:
        accepted = float(accepted)
        rejected = float(rejected)
    except (TypeError, ValueError):
        return 0.0

    total = accepted + rejected
    return rejected / total * 100.0 if total > 0 else 0.0


def get_voltage_mv(data, miner_type):
    if miner_type == "axeos":
        return int(data.get("coreVoltage", 0))
    return int(round(float(data.get("voltage", 0)) / 10))


def get_vr_temp(data):
    return float(data.get("vrTemp", data.get("vrtemp", data.get("temp2", -1))))
