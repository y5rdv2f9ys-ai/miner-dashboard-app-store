const list = document.getElementById('minerList');
const message = document.getElementById('message');

function escapeHtml(value) {
    return String(value)
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function showMessage(text, type) {
    message.textContent = text;
    message.className = `message ${type}`;
    message.hidden = false;
    window.scrollTo({top: 0, behavior: 'smooth'});
}

function numberInput(field, label, hint, value, step = '1') {
    return `
        <label>${label}
            <small>${hint}</small>
            <input name="${field}" type="number" step="${step}" value="${escapeHtml(value)}" required>
        </label>`;
}

function renderMiner(miner) {
    const card = document.createElement('article');
    card.className = 'miner-card';
    card.innerHTML = `
        <button class="miner-summary" type="button" aria-expanded="false">
            <div>
                <h2>${escapeHtml(miner.name)}</h2>
                <div class="miner-meta">${escapeHtml((miner.type || 'unknown').toUpperCase())} · ${escapeHtml(miner.ip)}</div>
            </div>
            <div class="live-state">
                <b>${miner.online ? `${Number(miner.current_temp).toFixed(1)}°C · ${miner.current_freq} MHz` : 'Offline'}</b>
                <span>${escapeHtml(miner.status)} · Thermal ${miner.enabled ? 'enabled' : 'disabled'}</span>
            </div>
        </button>
        <form class="miner-form">
            <input type="hidden" name="name" value="${escapeHtml(miner.name)}">
            <div class="enable-row">
                <div>
                    <strong>Thermal management</strong>
                    <small>Controls this miner on the next 60-second cycle.</small>
                </div>
                <input name="enabled" type="checkbox" ${miner.enabled ? 'checked' : ''} aria-label="Enable thermal management">
            </div>
            <div class="settings-grid">
                ${numberInput('base_freq', 'Base frequency', 'Normal operating MHz', miner.base_freq)}
                ${numberInput('hot_freq', 'Hot frequency', 'MHz at warning temperature', miner.hot_freq)}
                ${numberInput('critical_freq', 'Critical frequency', 'MHz at critical temperature', miner.critical_freq)}
                ${numberInput('recover_temp', 'Recovery temperature', 'Restore base frequency at or below °C', miner.recover_temp, '0.1')}
                ${numberInput('warn_temp', 'Warning temperature', 'Apply hot frequency at or above °C', miner.warn_temp, '0.1')}
                ${numberInput('critical_temp', 'Critical temperature', 'Apply critical frequency at or above °C', miner.critical_temp, '0.1')}
            </div>
            <div class="form-actions">
                <span class="save-note">Required: critical ≤ hot ≤ base and recovery &lt; warning &lt; critical.</span>
                <button class="save-button" type="submit">Save Settings</button>
            </div>
        </form>`;

    const summary = card.querySelector('.miner-summary');
    summary.addEventListener('click', () => {
        const open = card.classList.toggle('open');
        summary.setAttribute('aria-expanded', String(open));
    });

    card.querySelector('form').addEventListener('submit', saveSettings);
    return card;
}

async function saveSettings(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('.save-button');
    const data = Object.fromEntries(new FormData(form).entries());
    const payload = {
        name: data.name,
        enabled: form.elements.enabled.checked,
        base_freq: Number(data.base_freq),
        hot_freq: Number(data.hot_freq),
        critical_freq: Number(data.critical_freq),
        recover_temp: Number(data.recover_temp),
        warn_temp: Number(data.warn_temp),
        critical_temp: Number(data.critical_temp),
    };

    const action = payload.enabled ? 'save these settings and enable thermal control' : 'save these settings with thermal control disabled';
    if (!confirm(`Confirm you want to ${action} for ${payload.name}?`)) return;

    button.disabled = true;
    button.textContent = 'Saving...';
    try {
        const response = await fetch('/api/thermal-settings', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `Save failed (${response.status})`);
        showMessage(`${payload.name} settings saved. They will be loaded on the next thermal cycle.`, 'success');
        await loadSettings();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Save Settings';
    }
}

async function loadSettings() {
    try {
        const response = await fetch('/api/thermal-settings', {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Load failed (${response.status})`);
        list.innerHTML = '';
        data.miners.forEach(miner => list.appendChild(renderMiner(miner)));
        if (!data.miners.length) list.innerHTML = '<div class="loading">No configured miners found.</div>';
    } catch (error) {
        list.innerHTML = '';
        showMessage(error.message, 'error');
    }
}

loadSettings();
