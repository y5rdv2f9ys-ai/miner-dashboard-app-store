let latestMiners = [];
let latestThermalCounts = {};

const MINER_DISPLAY_ORDER = [
    'BitaxeBTC',
    'BitaxeBCH',
    'Bitaxe001',
    'Bitaxe002',
    'Bitaxe003',
    'Bitaxe004',
    'NQaxe',
    'NOctaxe'
];

const minerOrderIndex = new Map(
    MINER_DISPLAY_ORDER.map((name, index) => [name, index])
);

function sortMiners(miners) {
    return [...miners].sort((a, b) => {
        const aIndex = minerOrderIndex.get(a.name) ?? MINER_DISPLAY_ORDER.length;
        const bIndex = minerOrderIndex.get(b.name) ?? MINER_DISPLAY_ORDER.length;
        return aIndex - bIndex || a.name.localeCompare(b.name);
    });
}

function el(id) {
    return document.getElementById(id);
}

function setText(id, value) {
    const node = el(id);
    if (node) node.textContent = value;
}

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function onlineColorClass(online, total) {
    if (online === total) return 'green-text';
    if (online === 0) return 'red-text';
    return 'yellow-text';
}

function numeric(value) {
    const result = Number(value);
    return value === null || value === undefined || !Number.isFinite(result) ? null : result;
}

function metricValue(value, decimals = 1, suffix = '') {
    const result = numeric(value);
    return result === null ? '—' : `${result.toFixed(decimals)}${suffix}`;
}

function alertsColorClass(alerts) {
    if (alerts >= 5) return 'red-text';
    if (alerts >= 3) return 'orange-text';
    if (alerts > 0) return 'yellow-text';
    return 'green-text';
}

function healthColorClass(health) {
    if (health < 70) return 'red-text';
    if (health < 85) return 'orange-text';
    if (health < 95) return 'yellow-text';
    return 'green-text';
}

function fmtRun(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor((seconds % 86400) / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    if (days) return `${days}d ${hours}h`;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
}

function formatOddsDen(value) {
    if (!value || value <= 0 || !Number.isFinite(value)) return 'pending';
    return `1 in ${Math.round(value).toLocaleString()}`;
}

function formatDifficulty(value) {
    value = Number(value || 0);
    if (value >= 1e15) return `${(value / 1e15).toFixed(2)}P`;
    if (value >= 1e12) return `${(value / 1e12).toFixed(2)}T`;
    if (value >= 1e9) return `${(value / 1e9).toFixed(2)}G`;
    if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
    if (value >= 1e3) return `${(value / 1e3).toFixed(2)}K`;
    return value ? value.toFixed(0) : '--';
}

function btc(value) {
    return `${Number(value || 0).toFixed(8)} BTC`;
}

function compactBtc(value) {
    return Number(value || 0).toFixed(8);
}

function poolMiners(miners, pool) {
    return miners.filter(miner => miner.pool === pool);
}

function poolHash(miners, pool) {
    return poolMiners(miners, pool).reduce((sum, miner) => sum + (miner.th || 0), 0);
}

function difficultyPct(best, network) {
    if (!best || !network) return '--';
    const value = (best / network) * 100;
    if (value < 0.0001) return `${value.toExponential(2)}%`;
    return `${value.toFixed(4)}%`;
}

function renderMinerDashboard(data) {
    const miners = sortMiners(data.miners || []);
    const grid = el('minerGrid');
    grid.innerHTML = `<div class="live-fleet-head" aria-hidden="true">
        <span>State</span><span>Miner</span><span>TH/s</span><span>ASIC</span><span>VR</span><span>MHz</span><span>Thermal</span>
    </div>`;
    let totalTH = 0;

    miners.forEach(miner => {
        totalTH += numeric(miner.th) || 0;
        const offsite = miner.location_scope === 'OFF-SITE';
        const remoteTelemetry = miner.telemetry_source === 'BRAIINS';
        const management = miner.management || (offsite ? 'UNMANAGED' : 'MANAGED');
        const unmanaged = management === 'UNMANAGED';
        const thermalStatus = unmanaged ? 'UNMANAGED' : (miner.thermal_status || miner.status || '—');
        const hash = metricValue(miner.th, 2);
        const inactive = offsite && !miner.online;
        const state = offsite
            ? (inactive ? 'OFF-SITE INACTIVE' : 'OFF-SITE')
            : (miner.online ? '' : 'OFFLINE');
        const secondary = offsite
            ? (inactive ? 'Remote · inactive' : 'Remote · active')
            : '';
        const row = document.createElement('div');
        row.className = `live-fleet-row ${offsite ? 'is-offsite' : 'is-local'} ${inactive ? 'is-inactive' : ''} ${unmanaged ? 'is-unmanaged' : ''}`;
        row.innerHTML = `
            <div class="fleet-state">${state ? escapeHtml(state) : '<span class="local-dot" title="Local and online">●</span>'}</div>
            <div class="fleet-miner">${escapeHtml(miner.name)}${secondary ? `<small>${escapeHtml(secondary)}</small>` : ''}</div>
            <div class="fleet-hash"><b>${hash}</b><small> TH/s</small></div>
            <div class="fleet-asic">${remoteTelemetry ? '—' : metricValue(miner.temp, 1, '°')}</div>
            <div class="fleet-vr">${remoteTelemetry || miner.vr_temp === -1 ? '—' : metricValue(miner.vr_temp, 1, '°')}</div>
            <div class="fleet-mhz">${remoteTelemetry ? '—' : metricValue(miner.freq, 0)}</div>
            <div class="fleet-thermal ${escapeHtml(thermalStatus.replaceAll(' ', '-'))}">${escapeHtml(thermalStatus)}</div>`;
        grid.appendChild(row);
    });

    const health = Number.isFinite(data.health) ? data.health : null;
    const alerts = Number.isFinite(data.alert_count) ? data.alert_count : null;
    const summary = data.fleet_summary || {};
    const active = Number.isFinite(summary.active) ? summary.active : 0;
    const total = Number.isFinite(summary.total) ? summary.total : miners.length;
    setText('updated', `Updated: ${data.updated}`);
    setText('mobileTotal', `${totalTH.toFixed(2)} TH`);
    el('mobileSummary').innerHTML =
        `<span class="${onlineColorClass(active, total)}">Active ${active}/${total}</span>` +
        ` | <span class="${health === null ? '' : healthColorClass(health)}">Health ${health === null ? '--' : health + '%'}</span>` +
        ` | <span class="${alerts === null ? '' : alertsColorClass(alerts)}">Alerts ${alerts === null ? '--' : alerts}</span>`;
    setText('fleetBreakdown',
        `Local ${summary.local_online ?? 0}/${summary.local_total ?? 0} online · ` +
        `Off-site ${summary.offsite_mining ?? 0}/${summary.offsite_total ?? 0} mining`);

    const system = data.system_status || {};
    el('thermalMgmtStatus').innerHTML = `Thermal Management <span style="color:${system.thermal_management ? '#4ade80' : '#ef4444'}">●</span> ${system.thermal_management ? 'Online' : 'Offline'}`;
    el('minerLoggingStatus').innerHTML = `Miner Logging <span style="color:${system.miner_logging ? '#4ade80' : '#ef4444'}">●</span> ${system.miner_logging ? 'Online' : 'Offline'}`;
}

function renderStrategy(data) {
    const miners = data.miners || [];
    const btcMiners = poolMiners(miners, 'Umbrel Solo');
    const bchMiners = poolMiners(miners, 'BCH SoloPool');
    const braiinsMiners = poolMiners(miners, 'Braiins');
    const btcTH = poolHash(miners, 'Umbrel Solo');
    const bchTH = poolHash(miners, 'BCH SoloPool');
    const braiinsTH = poolHash(miners, 'Braiins');
    const total = btcTH + bchTH + braiinsTH;
    const pct = value => total ? (value / total) * 100 : 0;
    const odds = data.odds || {};
    const btcOdds = odds['Umbrel Solo'] || {};
    const bchOdds = odds['BCH SoloPool'] || {};
    const braiins = data.braiins || {};
    const solo = data.solopool || {};
    const activeBraiinsWorkers = (data.braiins_workers || []).filter(
        worker => worker.hash_rate_5m_th > 0 || worker.hash_rate_60m_th > 0
    );
    const btcSessionBest = Math.max(...btcMiners.map(m => m.best_session_diff || 0), 0);
    const btcHistoricBest = Math.max(...btcMiners.map(m => m.best_diff || 0), 0);
    const bchSessionBest = Math.max(...bchMiners.map(m => m.best_session_diff || 0), 0);
    const bchHistoricBest = Math.max(
        ...bchMiners.map(m => m.best_diff || 0),
        Number(solo.best_share || 0),
        0
    );

    setText('strategyUpdated', `Updated: ${data.updated}`);
    setText('strategyTotal', `${total.toFixed(2)} TH`);
    el('allocBtc').style.width = `${pct(btcTH)}%`;
    el('allocBch').style.width = `${pct(bchTH)}%`;
    el('allocBraiins').style.width = `${pct(braiinsTH)}%`;
    el('allocationLegend').innerHTML =
        `<span class="btc-label">BTC Solo ${pct(btcTH).toFixed(1)}%</span>` +
        `<span class="bch-label">BCH Solo ${pct(bchTH).toFixed(1)}%</span>` +
        `<span class="braiins-label">Braiins ${pct(braiinsTH).toFixed(1)}%</span>`;

    const soloPools = data.solo_pools || {};
    renderSoloAssignment('btc', soloPools['Umbrel Solo'] || {});
    setText('btcSessionBest', formatDifficulty(btcSessionBest));
    setText('btcHistoricBest', formatDifficulty(btcHistoricBest));
    setText('btcNetworkDiff', formatDifficulty(btcOdds.difficulty));
    setText('btcBestPct', difficultyPct(btcHistoricBest, btcOdds.difficulty));
    setText('btcSoloDay', formatOddsDen(btcOdds.day_den));
    setText('btcSoloMonth', formatOddsDen(btcOdds.month_den));

    renderSoloAssignment('bch', soloPools['BCH SoloPool'] || {});
    setText('bchSessionBest', formatDifficulty(bchSessionBest));
    setText('bchHistoricBest', formatDifficulty(bchHistoricBest));
    setText('bchNetworkDiff', formatDifficulty(bchOdds.difficulty));
    setText('bchBestPct', difficultyPct(bchHistoricBest, bchOdds.difficulty));
    setText('bchSoloDay', formatOddsDen(bchOdds.day_den));
    setText('bchSoloMonth', formatOddsDen(bchOdds.month_den));

    setText('braiinsHash', `${braiinsTH.toFixed(2)} TH`);
    setText('braiins60m', `${Number(braiins.hash_rate_60m_th || 0).toFixed(2)} TH`);
    setText('braiinsToday', compactBtc(braiins.today_reward));
    setText('braiinsBalance', compactBtc(braiins.current_balance));

    const workerList = el('braiinsWorkerList');
    const workers = activeBraiinsWorkers.sort((a, b) => b.hash_rate_5m_th - a.hash_rate_5m_th);
    const maxWorkerTH = Math.max(...workers.map(worker => worker.hash_rate_5m_th), 1);
    workerList.innerHTML = workers.length ? workers.map(worker => `
        <div class="braiins-worker">
            <span>${escapeHtml(worker.name)}</span>
            <div><i style="width:${Math.max(3, (worker.hash_rate_5m_th / maxWorkerTH) * 100)}%"></i></div>
            <b>${worker.hash_rate_5m_th.toFixed(2)} TH</b>
            <small>${escapeHtml(worker.scope)} · ${escapeHtml(String(worker.state || 'unknown').toUpperCase())}</small>
        </div>`).join('') : 'Braiins API unavailable or no workers';
}

function renderSoloAssignment(prefix, summary) {
    const miners = summary.assigned_miners || [];
    const activeCount = summary.active_count ?? 0;
    const assignedCount = summary.assigned_count ?? miners.length;
    setText(`${prefix}SoloHash`, `${Number(summary.current_hashrate_th || 0).toFixed(2)} TH`);
    setText(`${prefix}SoloMiners`, `${activeCount}/${assignedCount} active · ${assignedCount} assigned`);
    const list = el(`${prefix}SoloMinerList`);
    list.innerHTML = miners.length ? miners.map(miner => `
        <div class="solo-miner-row">
            <span>${escapeHtml(miner.name)}</span>
            <small class="${miner.active ? 'active' : 'offline'}">${miner.active ? 'ACTIVE' : 'OFFLINE'}</small>
            <b>${Number(miner.hashrate_th || 0).toFixed(2)} TH</b>
        </div>`).join('') : '<div class="solo-empty">No assigned miners</div>';
}

function fmt(value, decimals, suffix) {
    if (value === null || value === undefined) return '—';
    return value.toFixed(decimals) + suffix;
}

async function loadPerformance() {
    const response = await fetch('/api/performance');
    const data = await response.json();
    const rows = el('perfRows');
    rows.innerHTML = '';
    const nowMap = Object.fromEntries(latestMiners.map(miner => [miner.name, miner]));

    sortMiners(data.performance || []).forEach(perf => {
        const now = nowMap[perf.name] || {};
        const remoteTelemetry = now.telemetry_source === 'BRAIINS';
        const nowTH = numeric(perf.th_now) ?? numeric(now.th);
        let cls = '';
        if (numeric(perf.th_60m) > 0 && nowTH !== null) {
            const ratio = nowTH / perf.th_60m;
            cls = ratio >= 0.98 ? 'perf-good' : ratio >= 0.94 ? 'perf-warn' : 'perf-low';
        }
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(perf.name.replace('Bitaxe', 'BAxe'))}</td>
            <td class="${cls}"><b>${metricValue(nowTH, 2)}</b><br><small>${remoteTelemetry ? '—' : metricValue(now.freq, 0, ' MHz')}</small></td>
            <td>${fmt(perf.th_60m, 2, '')}</td><td>${fmt(perf.th_12h, 2, '')}</td>
            <td>${fmt(perf.th_24h, 2, '')}</td>
            <td>${remoteTelemetry ? '—/—' : `${metricValue(now.temp, 1)}/${numeric(now.vr_temp) !== null && Number(now.vr_temp) >= 0 ? metricValue(now.vr_temp, 1) : '—'}°`}</td>`;
        rows.appendChild(row);
    });

    const snapshotCounts = latestThermalCounts;
    const counts = {STABLE: 0, HOLDING: 0, COOLING: 0, 'MAX COOLING': 0, BENCHMARK: 0, ...snapshotCounts};
    el('thermalStrip').innerHTML =
        `<span class="stable">Stable ${counts.STABLE}</span>&nbsp;&nbsp;` +
        `<span class="holding">Holding ${counts.HOLDING}</span>&nbsp;&nbsp;` +
        `<span class="cooling">Cooling ${counts.COOLING}</span>&nbsp;&nbsp;` +
        `<span class="maxcool">Max ${counts['MAX COOLING']}</span>&nbsp;&nbsp;` +
        `<span class="benchmark">Benchmark ${counts.BENCHMARK}</span>`;
    setText('perfUpdated', `Updated: ${data.updated} · rolling averages`);
}

async function resetAllRunsLogs() {
    if (!confirm('Clear performance history, mining run counters, and the thermal log? Miner settings, thermal settings, Discord config, and live current odds will not be changed.')) return;
    const response = await fetch('/reset_all_runs_logs', {method: 'POST'});
    if (!response.ok) {
        alert('Could not clear history and thermal log.');
        return;
    }
    location.reload();
}

async function sendDiscordTest() {
    const button = document.querySelector('.discord-test-btn');
    const status = el('discordTestStatus');
    if (button) button.disabled = true;
    if (status) status.textContent = 'Sending...';

    try {
        const response = await fetch('/api/discord/test', {method: 'POST'});
        const data = await response.json().catch(() => ({}));
        if (!response.ok || !data.ok) {
            throw new Error(data.error || 'Discord test failed');
        }
        if (status) status.textContent = 'Sent';
    } catch (error) {
        if (status) status.textContent = 'Failed';
    } finally {
        if (button) button.disabled = false;
        setTimeout(() => {
            if (status) status.textContent = '';
        }, 5000);
    }
}

async function loadData() {
    const response = await fetch('/api/miners');
    if (!response.ok) throw new Error(`Dashboard API returned ${response.status}`);
    const data = await response.json();
    latestMiners = data.miners || [];
    latestThermalCounts = data.thermal_counts || {};
    renderMinerDashboard(data);
    renderStrategy(data);
    await loadPerformance();
}

const swipe = el('swipeContainer');
swipe.addEventListener('scroll', () => {
    const page = Math.round(swipe.scrollLeft / window.innerWidth);
    for (let index = 0; index < 3; index++) {
        el(`dot${index + 1}`).classList.toggle('active', page === index);
    }
});

loadData().catch(error => console.error('Dashboard refresh failed:', error));
setInterval(() => loadData().catch(error => console.error('Dashboard refresh failed:', error)), 10000);
