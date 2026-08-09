const message = document.getElementById('message');
const minerSelect = document.getElementById('minerSelect');
const candidateSelect = document.getElementById('candidateSelect');
const sessionIdInput = document.getElementById('sessionId');
const statusLine = document.getElementById('statusLine');
const prepareButton = document.getElementById('prepareButton');
const runButton = document.getElementById('runButton');
const runFullButton = document.getElementById('runFullButton');
const cancelButton = document.getElementById('cancelButton');
const exportButton = document.getElementById('exportButton');
const refreshButton = document.getElementById('refreshButton');
const recoveryPanel = document.getElementById('recoveryPanel');
const recoveryText = document.getElementById('recoveryText');
const recoveryDetails = document.getElementById('recoveryDetails');
const retryRestoreButton = document.getElementById('retryRestoreButton');
const confirmManualRestoreButton = document.getElementById('confirmManualRestoreButton');
const baselineBox = document.getElementById('baselineBox');
const profileBox = document.getElementById('profileBox');
const resultBox = document.getElementById('resultBox');
const timerBox = document.getElementById('timerBox');
const candidateRows = document.getElementById('candidateRows');
const recommendationBox = document.getElementById('recommendationBox');

let state = {
    miners: [],
    benchmark: null,
    report: null,
    pollTimer: null,
    countdownTimer: null,
};

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function showMessage(text, type = 'success') {
    message.textContent = text;
    message.className = `message ${type}`;
    message.hidden = false;
}

async function fetchJson(path) {
    const response = await fetch(path, {cache: 'no-store'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
}

async function postJson(path, payload) {
    const response = await fetch(path, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `Request failed (${response.status})`);
    return result;
}

function metric(label, value) {
    return `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value ?? 'NA')}</strong></div>`;
}

function parseTime(value) {
    const time = Date.parse(value || '');
    return Number.isFinite(time) ? time : null;
}

function formatDuration(seconds) {
    if (seconds == null || !Number.isFinite(seconds)) return 'NA';
    seconds = Math.max(0, Math.ceil(seconds));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m`;
    return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

function timingPayload() {
    const session = activeSession();
    const report = activeReport();
    const timing = session?.benchmark_plan?.timing || report?.profile?.timing || {};
    const warmup = Number(timing.warmup_seconds || 0);
    const test = Number(timing.test_seconds || 0);
    const candidate = Number(timing.candidate_seconds || (warmup + test) || 0);
    return {
        warmup_seconds: warmup,
        test_seconds: test,
        candidate_seconds: candidate,
    };
}

function activeCandidateRow() {
    const report = activeReport();
    const rows = report?.results || [];
    const runner = activeRunner();
    if (runner?.sequence) {
        const runnerRow = rows.find(row => Number(row.sequence) === Number(runner.sequence));
        if (runnerRow) return runnerRow;
    }
    return rows.find(row => row.status === 'applied') || null;
}

function candidateRemainingSeconds(row, candidateSeconds, now = Date.now()) {
    if (!row || row.status !== 'applied' || !candidateSeconds) return null;
    const appliedAt = parseTime(row.applied_at || row.updated_at);
    if (!appliedAt) return null;
    return candidateSeconds - ((now - appliedAt) / 1000);
}

function activeSession() {
    return state.benchmark?.active || null;
}

function activeReport() {
    const active = activeSession();
    if (!active) return state.benchmark?.latest_report || state.report || null;
    return state.benchmark?.results?.[active.session_id] || state.benchmark?.active_results || null;
}

function activeRunner() {
    return state.benchmark?.runner || null;
}

function runnerIsActive() {
    return activeRunner()?.status === 'running';
}

function pendingRecoveries() {
    return state.benchmark?.recovery_required || [];
}

function renderRecovery() {
    const recoveries = pendingRecoveries();
    const recovery = recoveries[0];
    recoveryPanel.hidden = !recovery;
    if (!recovery) return;
    const restore = recovery.restore || {};
    const extra = recoveries.length > 1 ? ` ${recoveries.length - 1} additional recovery item(s) are also pending.` : '';
    recoveryText.textContent = `Thermal management remains locked for this miner until its saved settings are restored and confirmed.${extra}`;
    recoveryDetails.innerHTML = [
        metric('Miner', recovery.miner),
        metric('Session', recovery.session_id),
        metric('Restore target', `${restore.frequency ?? 'NA'} MHz / ${restore.voltage ?? 'NA'} mV`),
        metric('Last error', recovery.last_restore_error || recovery.reason),
        metric('Attempts', recovery.restore_attempts || 0),
    ].join('');
    retryRestoreButton.dataset.sessionId = recovery.session_id || '';
    confirmManualRestoreButton.dataset.sessionId = recovery.session_id || '';
}

function renderMiners() {
    const current = minerSelect.value;
    minerSelect.innerHTML = '';
    state.miners.forEach(miner => {
        const option = document.createElement('option');
        option.value = miner.name;
        option.textContent = `${miner.name} (${miner.type || 'unknown'} · ${miner.ip})`;
        minerSelect.appendChild(option);
    });
    if (current && state.miners.some(miner => miner.name === current)) {
        minerSelect.value = current;
    }
}

function renderSummary() {
    const session = activeSession();
    const report = activeReport();
    const baseline = session?.benchmark_plan?.baseline || report?.restore_baseline || {};
    const profile = report?.profile || {};
    const counts = report?.counts || {};
    sessionIdInput.value = session?.session_id || '';
    const runner = activeRunner();
    const runnerText = runner
        ? ` · ${runner.mode === 'full' ? 'full benchmark' : 'candidate'} #${runner.sequence} ${runner.status}${runner.mode === 'full' ? ` · ${runner.completed_candidates || 0}/${runner.total_candidates || 0}` : ''}`
        : '';
    statusLine.textContent = session
        ? `${session.miner} · ${session.state} · ${session.device_profile_label || 'profile pending'}${runnerText}`
        : 'No active benchmark session.';

    baselineBox.innerHTML = [
        metric('Live freq', baseline.frequency),
        metric('Live volt', baseline.voltage),
        metric('Base freq', baseline.base_frequency),
        metric('Base volt', baseline.base_voltage),
    ].join('');
    profileBox.innerHTML = [
        metric('Profile', profile.label || session?.device_profile_label),
        metric('Freq range', profile.frequency ? `${profile.frequency.min}-${profile.frequency.max}` : null),
        metric('Volt range', profile.voltage ? `${profile.voltage.min}-${profile.voltage.max}` : null),
        metric('Chip cutoff', profile.safety?.max_chip_temp),
    ].join('');
    resultBox.innerHTML = [
        metric('Total', counts.total || 0),
        metric('Planned', counts.planned || 0),
        metric('Sampled', counts.sampled || 0),
        metric('Aborted', counts.aborted || 0),
    ].join('');
    renderTimers();
    renderRecommendations();
}

function formatNumber(value, digits = 2) {
    return value == null ? 'NA' : Number(value).toFixed(digits);
}

function renderRecommendations() {
    const recommendations = activeReport()?.recommendations || state.report?.recommendations;
    if (!recommendations) {
        recommendationBox.textContent = 'Complete a full benchmark to generate recommendations.';
        return;
    }
    const entries = [
        recommendations.best_hashrate,
        recommendations.best_stability,
        recommendations.lowest_power,
        recommendations.best_efficiency,
        recommendations.best_overall,
    ];
    recommendationBox.innerHTML = entries.map(item => {
        if (!item) return '<article class="summary-card"><h3>Best Stability</h3><p>Insufficient samples.</p></article>';
        const s = item.sample_summary || {};
        const d = item.baseline_delta || {};
        const delta = (key, unit) => d[key] == null ? '' : ` (${d[key] >= 0 ? '+' : ''}${formatNumber(d[key])}${unit} vs baseline)`;
        return `<article class="summary-card"><h3>${escapeHtml(item.category)}</h3><div class="metric-list">
            ${metric('Setting', `${item.frequency} MHz / ${item.voltage} mV`)}
            ${metric('Hashrate', `${formatNumber(s.average_hashrate_th)} TH/s${delta('average_hashrate_th', ' TH/s')}`)}
            ${metric('Power', `${formatNumber(s.average_power_watts)} W${delta('average_power_watts', ' W')}`)}
            ${metric('Efficiency', `${formatNumber(s.efficiency_jth)} J/TH${delta('efficiency_jth', ' J/TH')}`)}
            ${metric('Chip temp', `${formatNumber(s.average_temp, 1)} avg / ${formatNumber(s.max_temp, 1)} max${delta('average_temp', '°C')}`)}
            ${metric('VR temp', `${formatNumber(s.average_vr_temp, 1)} avg / ${formatNumber(s.max_vr_temp, 1)} max${delta('average_vr_temp', '°C')}`)}
            ${metric('Stability', `${formatNumber(s.hashrate_variability_pct)}% variability (${s.sample_count || 0} samples)`)}
        </div></article>`;
    }).join('');
}

function renderTimers() {
    const report = activeReport();
    const rows = report?.results || [];
    const timing = timingPayload();
    const candidateSeconds = timing.candidate_seconds;
    const current = activeCandidateRow();
    const currentRemaining = candidateRemainingSeconds(current, candidateSeconds);
    const plannedCount = rows.filter(row => row.status === 'planned').length;
    const overallRemaining = candidateSeconds
        ? Math.max(0, (currentRemaining || 0)) + (plannedCount * candidateSeconds)
        : null;

    timerBox.innerHTML = [
        metric('Candidate', current?.status === 'applied' ? formatDuration(currentRemaining) : 'Idle'),
        metric('Overall est', activeSession() ? formatDuration(overallRemaining) : 'Idle'),
        metric('Per candidate', candidateSeconds ? formatDuration(candidateSeconds) : 'NA'),
        metric('Warmup + test', timing.warmup_seconds || timing.test_seconds
            ? `${formatDuration(timing.warmup_seconds)} + ${formatDuration(timing.test_seconds)}`
            : 'NA'),
    ].join('');
}

function candidateLabel(row) {
    return `#${row.sequence} · ${row.frequency} MHz · ${row.voltage} mV · ${row.status}`;
}

function renderCandidates() {
    const report = activeReport();
    const rows = report?.results || [];
    candidateSelect.innerHTML = '';
    candidateRows.innerHTML = '';
    if (!rows.length) {
        candidateRows.innerHTML = '<tr><td colspan="8">No benchmark candidates yet.</td></tr>';
        return;
    }
    rows.forEach(row => {
        const option = document.createElement('option');
        option.value = row.sequence;
        option.textContent = candidateLabel(row);
        option.disabled = !['planned', 'applied'].includes(row.status);
        candidateSelect.appendChild(option);

        const summary = row.sample_summary || {};
        const tr = document.createElement('tr');
        if (String(row.sequence) === candidateSelect.value) tr.className = 'selected';
        tr.innerHTML = `
            <td>${escapeHtml(row.sequence)}</td>
            <td>${escapeHtml(row.frequency)} MHz<br><small>${escapeHtml(row.frequency_relation)}</small></td>
            <td>${escapeHtml(row.voltage)} mV<br><small>${escapeHtml(row.voltage_relation)}</small></td>
            <td class="status-${escapeHtml(row.status)}">${escapeHtml(row.status)}</td>
            <td>${escapeHtml(row.safety_decision || '')}</td>
            <td>${summary.average_hashrate_th == null ? '' : escapeHtml(summary.average_hashrate_th.toFixed(2))}</td>
            <td>${summary.average_temp == null ? '' : escapeHtml(summary.average_temp.toFixed(1))}</td>
            <td>${summary.efficiency_jth == null ? '' : escapeHtml(summary.efficiency_jth.toFixed(2))}</td>`;
        tr.addEventListener('click', () => {
            candidateSelect.value = row.sequence;
            renderCandidates();
        });
        candidateRows.appendChild(tr);
    });
}

function renderControls() {
    const session = activeSession();
    const running = runnerIsActive();
    prepareButton.disabled = Boolean(session) || pendingRecoveries().length > 0;
    runButton.disabled = !session || !candidateSelect.value || running;
    runFullButton.disabled = !session || running || !(activeReport()?.results || []).some(row => row.status === 'planned');
    cancelButton.disabled = !session;
    exportButton.disabled = !activeReport();
}

async function runFullBenchmark() {
    const session = activeSession();
    if (!session) return;
    const remaining = (activeReport()?.results || []).filter(row => row.status === 'planned').length;
    if (!confirm(`Run all ${remaining} remaining candidates unattended?`)) return;
    runFullButton.disabled = true;
    try {
        await postJson('/api/benchmark/run-full', {session_id: session.session_id});
        showMessage(`Full benchmark started for ${remaining} candidates.`, 'success');
        await loadState();
    } catch (error) {
        showMessage(error.message, 'error');
        await loadState();
    }
}

function render() {
    renderMiners();
    renderRecovery();
    renderSummary();
    renderCandidates();
    renderControls();
    if (state.countdownTimer) clearInterval(state.countdownTimer);
    if (activeSession()) {
        state.countdownTimer = setInterval(renderTimers, 1000);
    }
}

async function retryRestore() {
    const sessionId = retryRestoreButton.dataset.sessionId;
    if (!sessionId) return;
    retryRestoreButton.disabled = true;
    try {
        await postJson('/api/benchmark/retry-restore', {session_id: sessionId});
        showMessage('Saved miner settings were restored and the thermal lock was released.', 'success');
        await loadState();
    } catch (error) {
        showMessage(`Restore retry failed: ${error.message}`, 'error');
        await loadState();
    } finally {
        retryRestoreButton.disabled = false;
    }
}

async function confirmManualRestore() {
    const sessionId = confirmManualRestoreButton.dataset.sessionId;
    if (!sessionId) return;
    if (!confirm('Confirm that you manually restored the exact saved frequency and voltage shown above? This will release the thermal lock.')) return;
    confirmManualRestoreButton.disabled = true;
    try {
        await postJson('/api/benchmark/confirm-manual-restore', {session_id: sessionId});
        showMessage('Manual restore confirmed and thermal management resumed.', 'success');
        await loadState();
    } catch (error) {
        showMessage(error.message, 'error');
        await loadState();
    } finally {
        confirmManualRestoreButton.disabled = false;
    }
}

async function loadState() {
    const [miners, benchmark] = await Promise.all([
        fetchJson('/api/miner-management'),
        fetchJson('/api/benchmark'),
    ]);
    state.miners = miners.miners || [];
    state.benchmark = benchmark;
    state.report = benchmark.latest_report || state.report;
    const active = activeSession();
    if (active) {
        state.report = await fetchJson(`/api/benchmark/report?session_id=${encodeURIComponent(active.session_id)}`);
        state.benchmark.results = state.benchmark.results || {};
        state.benchmark.results[active.session_id] = state.report;
    }
    render();
    if (state.pollTimer) clearTimeout(state.pollTimer);
    if (runnerIsActive()) {
        state.pollTimer = setTimeout(() => {
            loadState().catch(error => showMessage(error.message, 'error'));
        }, 10000);
    }
}

async function prepareBenchmark() {
    const miner = minerSelect.value;
    if (!miner) return;
    prepareButton.disabled = true;
    try {
        const result = await postJson('/api/benchmark/prepare', {miner});
        showMessage(`Prepared ${result.session.miner}.`, 'success');
        await loadState();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        prepareButton.disabled = false;
        renderControls();
    }
}

async function runCandidate() {
    const session = activeSession();
    const sequence = Number(candidateSelect.value);
    if (!session || !sequence) return;
    if (!confirm(`Run candidate #${sequence}?`)) return;
    runButton.disabled = true;
    try {
        await postJson('/api/benchmark/run-candidate', {
            session_id: session.session_id,
            sequence,
        });
        showMessage(`Candidate #${sequence} started.`, 'success');
        await loadState();
    } catch (error) {
        showMessage(error.message, 'error');
        await loadState();
    } finally {
        runButton.disabled = false;
        renderControls();
    }
}

async function cancelActive() {
    const session = activeSession();
    if (!session) return;
    if (!confirm(`Cancel active benchmark for ${session.miner}?`)) return;
    cancelButton.disabled = true;
    try {
        await postJson('/api/benchmark/cancel-active', {session_id: session.session_id});
        showMessage('Benchmark canceled.', 'success');
        await loadState();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        cancelButton.disabled = false;
        renderControls();
    }
}

function exportReport() {
    const report = activeReport() || state.report;
    if (!report) return;
    const blob = new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'});
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = `benchmark-${report.session?.session_id || 'report'}.json`;
    link.click();
    URL.revokeObjectURL(link.href);
}

prepareButton.addEventListener('click', prepareBenchmark);
runButton.addEventListener('click', runCandidate);
runFullButton.addEventListener('click', runFullBenchmark);
cancelButton.addEventListener('click', cancelActive);
exportButton.addEventListener('click', exportReport);
refreshButton.addEventListener('click', loadState);
retryRestoreButton.addEventListener('click', retryRestore);
confirmManualRestoreButton.addEventListener('click', confirmManualRestore);
candidateSelect.addEventListener('change', renderCandidates);
loadState().catch(error => showMessage(error.message, 'error'));
