const list = document.getElementById('minerList');
const message = document.getElementById('message');
const addForm = document.getElementById('addMinerForm');
const scanButton = document.getElementById('scanButton');
const pendingPanel = document.getElementById('pendingPanel');
const pendingList = document.getElementById('pendingList');

function escapeHtml(value) {
    return String(value ?? '')
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

function textInput(field, label, value, attrs = '') {
    return `
        <label>${label}
            <input name="${field}" type="text" value="${escapeHtml(value)}" ${attrs}>
        </label>`;
}

function osSelect(value) {
    return `
        <label>Miner OS
            <select name="type" required>
                <option value="axeos" ${value === 'axeos' ? 'selected' : ''}>AxeOS</option>
                <option value="nerdos" ${value === 'nerdos' ? 'selected' : ''}>NerdOS</option>
            </select>
        </label>`;
}

function formPayload(form) {
    const data = Object.fromEntries(new FormData(form).entries());
    let identity = {};
    if (data.identity_json) {
        try {
            const parsed = JSON.parse(data.identity_json);
            if (parsed && typeof parsed === 'object') identity = parsed;
        } catch (_) {
            identity = {};
        }
    }
    const payload = {
        original_name: data.original_name,
        name: data.name,
        ip: data.ip,
        type: data.type,
        pool: data.pool || '',
        coin: data.coin || '',
    };
    if (Object.keys(identity).length) payload.identity = identity;
    return payload;
}

function identitySummary(identity = {}) {
    const parts = [];
    if (identity.mac) parts.push(`MAC ${identity.mac}`);
    if (identity.hostname) parts.push(identity.hostname);
    if (identity.model) parts.push(identity.model);
    if (identity.version) parts.push(identity.version);
    return parts.join(' · ') || 'No stable identity reported';
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
            <div class="thermal-state">Thermal ${miner.enabled ? 'enabled' : 'disabled'}</div>
        </button>
        <form class="miner-form">
            <input type="hidden" name="original_name" value="${escapeHtml(miner.name)}">
            <input type="hidden" name="identity_json" value="${escapeHtml(JSON.stringify(miner.identity || {}))}">
            <div class="settings-grid">
                ${textInput('name', 'Miner name', miner.name, 'maxlength="48" required')}
                ${textInput('ip', 'IP address', miner.ip, 'inputmode="decimal" required')}
                ${osSelect(miner.type)}
                ${textInput('pool', 'Pool', miner.pool, 'maxlength="64"')}
                ${textInput('coin', 'Coin', miner.coin, 'maxlength="16"')}
            </div>
            <div class="form-actions">
                <span class="save-note">${escapeHtml(identitySummary(miner.identity))}</span>
                <button class="delete-button" type="button">Delete</button>
                <button class="save-button" type="submit">Save Miner</button>
            </div>
        </form>`;

    const summary = card.querySelector('.miner-summary');
    summary.addEventListener('click', () => {
        const open = card.classList.toggle('open');
        summary.setAttribute('aria-expanded', String(open));
    });

    card.querySelector('form').addEventListener('submit', saveMiner);
    card.querySelector('.delete-button').addEventListener('click', () => deleteMiner(miner.name));
    return card;
}

function fillAddForm(miner) {
    addForm.elements.name.value = miner.name || '';
    addForm.elements.ip.value = miner.ip || '';
    addForm.elements.type.value = miner.type || 'axeos';
    addForm.elements.pool.value = miner.pool || '';
    addForm.elements.coin.value = miner.coin || '';
    addForm.elements.identity_json.value = JSON.stringify(miner.identity || {});
    addForm.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function renderPendingMiner(miner) {
    const item = document.createElement('article');
    item.className = 'pending-card';
    item.innerHTML = `
        <div>
            <h3>${escapeHtml(miner.name || 'Discovered miner')}</h3>
            <div class="miner-meta">${escapeHtml((miner.type || 'unknown').toUpperCase())} · ${escapeHtml(miner.ip)}</div>
            <p>${escapeHtml(identitySummary(miner.identity))}</p>
        </div>
        <button type="button">Use in Add Form</button>`;
    item.querySelector('button').addEventListener('click', () => fillAddForm(miner));
    return item;
}

function renderPending(pending) {
    pendingList.innerHTML = '';
    if (!pending.length) {
        pendingPanel.hidden = true;
        return;
    }
    pending.forEach(miner => pendingList.appendChild(renderPendingMiner(miner)));
    pendingPanel.hidden = false;
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

async function addMiner(event) {
    event.preventDefault();
    const button = addForm.querySelector('.save-button');
    const payload = formPayload(addForm);
    if (!confirm(`Add ${payload.name} at ${payload.ip}?`)) return;

    button.disabled = true;
    button.textContent = 'Adding...';
    try {
        await postJson('/api/miner-management/add', payload);
        addForm.reset();
        addForm.elements.identity_json.value = '';
        showMessage(`${payload.name} added. Thermal management is disabled.`, 'success');
        await loadMiners();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Add Miner';
    }
}

async function scanMiners() {
    scanButton.disabled = true;
    scanButton.textContent = 'Scanning...';
    try {
        const result = await postJson('/api/miner-discovery/scan', {});
        const updated = result.updated || [];
        const pending = result.pending || [];
        renderPending(pending);
        let text = `Scan complete: ${result.discovered || 0} miner(s) found.`;
        if (updated.length) text += ` Updated ${updated.length} changed IP(s).`;
        if (pending.length) text += ` ${pending.length} new miner(s) pending.`;
        showMessage(text, 'success');
        await loadMiners();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        scanButton.disabled = false;
        scanButton.textContent = 'Scan Now';
    }
}

async function saveMiner(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('.save-button');
    const payload = formPayload(form);
    if (!confirm(`Save changes for ${payload.original_name}?`)) return;

    button.disabled = true;
    button.textContent = 'Saving...';
    try {
        await postJson('/api/miner-management/update', payload);
        showMessage(`${payload.name} saved.`, 'success');
        await loadMiners();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Save Miner';
    }
}

async function deleteMiner(name) {
    if (!confirm(`Delete ${name}?`)) return;

    try {
        await postJson('/api/miner-management/delete', {name});
        showMessage(`${name} deleted.`, 'success');
        await loadMiners();
    } catch (error) {
        showMessage(error.message, 'error');
    }
}

async function loadMiners() {
    try {
        const response = await fetch('/api/miner-management', {cache: 'no-store'});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `Load failed (${response.status})`);
        list.innerHTML = '';
        renderPending(data.pending || []);
        data.miners.forEach(miner => list.appendChild(renderMiner(miner)));
        if (!data.miners.length) list.innerHTML = '<div class="loading">No configured miners found.</div>';
    } catch (error) {
        list.innerHTML = '';
        showMessage(error.message, 'error');
    }
}

addForm.addEventListener('submit', addMiner);
scanButton.addEventListener('click', scanMiners);
loadMiners();
