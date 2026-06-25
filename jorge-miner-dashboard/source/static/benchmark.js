const message = document.getElementById('message');
const minerSelect = document.getElementById('minerSelect');
const candidateSelect = document.getElementById('candidateSelect');
const sessionIdInput = document.getElementById('sessionId');
const statusLine = document.getElementById('statusLine');
const prepareButton = document.getElementById('prepareButton');
const runButton = document.getElementById('runButton');
const cancelButton = document.getElementById('cancelButton');
const exportButton = document.getElementById('exportButton');
const refreshButton = document.getElementById('refreshButton');
const baselineBox = document.getElementById('baselineBox');
const profileBox = document.getElementById('profileBox');
const resultBox = document.getElementById('resultBox');
const candidateRows = document.getElementById('candidateRows');

let state = {
    miners: [],
    benchmark: null,
    report: null,
    pollTimer: null,
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

function activeSession() {
    return state.benchmark?.active || null;
}

function activeReport() {
    const active = activeSession();
    if (!active) return null;
    return state.benchmark?.results?.[active.session_id] || state.benchmark?.active_results || null;
}

function activeRunner() {
    return state.benchmark?.runner || null;
}

function runnerIsActive() {
    return activeRunner()?.status === 'running';
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
        ? ` · candidate #${runner.sequence} ${runner.status}`
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
    prepareButton.disabled = Boolean(session);
    runButton.disabled = !session || !candidateSelect.value || running;
    cancelButton.disabled = !session;
    exportButton.disabled = !activeReport();
}

function render() {
    renderMiners();
    renderSummary();
    renderCandidates();
    renderControls();
}

async function loadState() {
    const [miners, benchmark] = await Promise.all([
        fetchJson('/api/miner-management'),
        fetchJson('/api/benchmark'),
    ]);
    state.miners = miners.miners || [];
    state.benchmark = benchmark;
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
cancelButton.addEventListener('click', cancelActive);
exportButton.addEventListener('click', exportReport);
refreshButton.addEventListener('click', loadState);
candidateSelect.addEventListener('change', renderCandidates);
loadState().catch(error => showMessage(error.message, 'error'));
