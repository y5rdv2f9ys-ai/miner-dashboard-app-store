const list = document.getElementById('minerList');
const message = document.getElementById('message');
const addForm = document.getElementById('addMinerForm');
const scanButton = document.getElementById('scanButton');
const pendingPanel = document.getElementById('pendingPanel');
const pendingList = document.getElementById('pendingList');
const braiinsPanel = document.getElementById('braiinsPanel');
const braiinsList = document.getElementById('braiinsList');

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

function selected(value, expected) { return value === expected ? 'selected' : ''; }

function capabilityFields(miner = {}) {
    const source = miner.telemetry_source || 'LOCAL_API';
    const scope = miner.location_scope || 'LOCAL';
    return `
        <label>Location<select name="location_scope" required>
            <option value="LOCAL" ${selected(scope, 'LOCAL')}>On-site / Local</option>
            <option value="OFF-SITE" ${selected(scope, 'OFF-SITE')}>Off-site</option>
        </select></label>
        <label>Telemetry source<select name="telemetry_source" required>
            <option value="LOCAL_API" ${selected(source, 'LOCAL_API')}>Local miner API</option>
            <option value="BRAIINS" ${selected(source, 'BRAIINS')}>Braiins worker</option>
        </select></label>
        <label>Pool<select name="pool" required>
            <option value="" ${selected(miner.pool || '', '')}>Choose pool…</option>
            <option value="Braiins" ${selected(miner.pool, 'Braiins')}>Braiins</option>
            <option value="Umbrel Solo" ${selected(miner.pool, 'Umbrel Solo')}>BTC Solo</option>
            <option value="BCH SoloPool" ${selected(miner.pool, 'BCH SoloPool')}>BCH Solo</option>
        </select></label>
        <label class="braiins-field" ${source === 'BRAIINS' ? '' : 'hidden'}>Braiins worker name
            <input name="worker_name" type="text" maxlength="48" value="${escapeHtml(miner.worker_name || miner.name || '')}">
        </label>`;
}

function updateCapabilities(form) {
    const remote = form.elements.telemetry_source.value === 'BRAIINS';
    form.querySelectorAll('.local-api-field').forEach(field => field.hidden = remote);
    form.querySelectorAll('.braiins-field').forEach(field => field.hidden = !remote);
    form.elements.ip.required = !remote;
    form.elements.type.required = !remote;
    form.elements.worker_name.required = remote;
    if (remote && !form.elements.worker_name.value) form.elements.worker_name.value = form.elements.name.value;
}

function bindCapabilities(form) {
    form.elements.telemetry_source.addEventListener('change', () => updateCapabilities(form));
    form.elements.name.addEventListener('input', () => {
        if (form.elements.telemetry_source.value === 'BRAIINS' && !form.elements.worker_name.dataset.edited)
            form.elements.worker_name.value = form.elements.name.value;
    });
    form.elements.worker_name.addEventListener('input', () => form.elements.worker_name.dataset.edited = 'true');
    updateCapabilities(form);
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
        pool: data.pool,
        location_scope: data.location_scope,
        telemetry_source: data.telemetry_source,
        worker_name: data.worker_name || data.name,
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
    const sourceLabel = miner.telemetry_source === 'BRAIINS' ? 'BRAIINS TELEMETRY' : (miner.type || 'unknown').toUpperCase();
    const locationLabel = miner.location_scope === 'OFF-SITE' ? 'OFF-SITE' : 'LOCAL';
    const endpointLabel = miner.telemetry_source === 'BRAIINS' ? (miner.worker_name || miner.name) : miner.ip;
    const card = document.createElement('article');
    card.className = 'miner-card';
    card.innerHTML = `
        <button class="miner-summary" type="button" aria-expanded="false">
            <div>
                <h2>${escapeHtml(miner.name)}</h2>
                <div class="miner-meta">${escapeHtml(locationLabel)} · ${escapeHtml(sourceLabel)} · ${escapeHtml(endpointLabel)}</div>
            </div>
            <div class="thermal-state">Thermal ${miner.enabled ? 'enabled' : 'disabled'}</div>
        </button>
        <form class="miner-form">
            <input type="hidden" name="original_name" value="${escapeHtml(miner.name)}">
            <input type="hidden" name="identity_json" value="${escapeHtml(JSON.stringify(miner.identity || {}))}">
            <div class="settings-grid">
                ${textInput('name', 'Miner name', miner.name, 'maxlength="48" required')}
                <div class="local-api-field">${textInput('ip', 'IP address', miner.ip, 'inputmode="decimal"')}</div>
                <div class="local-api-field">${osSelect(miner.type || 'axeos')}</div>
                ${capabilityFields(miner)}
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
    bindCapabilities(card.querySelector('form'));
    card.querySelector('.delete-button').addEventListener('click', () => deleteMiner(miner.name));
    return card;
}

function fillAddForm(miner) {
    addForm.elements.name.value = miner.name || '';
    addForm.elements.ip.value = miner.ip || '';
    addForm.elements.type.value = miner.type || 'axeos';
    addForm.elements.pool.value = miner.pool || '';
    addForm.elements.location_scope.value = miner.location_scope ?? 'LOCAL';
    addForm.elements.telemetry_source.value = miner.telemetry_source || 'LOCAL_API';
    addForm.elements.worker_name.value = miner.worker_name || miner.name || '';
    addForm.elements.identity_json.value = JSON.stringify(miner.identity || {});
    updateCapabilities(addForm);
    addForm.scrollIntoView({behavior: 'smooth', block: 'start'});
}

function renderBraiinsWorkers(workers) {
    braiinsList.innerHTML = '';
    workers.forEach(worker => {
        const item = document.createElement('article');
        item.className = 'pending-card';
        item.innerHTML = `<div><h3>${escapeHtml(worker.name)}</h3><p>UNADOPTED / POOL-ONLY · ${escapeHtml(worker.state || 'unknown')}</p></div><button type="button">Adopt</button>`;
        item.querySelector('button').addEventListener('click', () => fillAddForm({
            name: worker.name, worker_name: worker.name, telemetry_source: 'BRAIINS',
            location_scope: '', pool: 'Braiins', ip: '', type: 'axeos'
        }));
        braiinsList.appendChild(item);
    });
    braiinsPanel.hidden = !workers.length;
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
        renderBraiinsWorkers(data.available_braiins_workers || []);
        data.miners.forEach(miner => list.appendChild(renderMiner(miner)));
        if (!data.miners.length) list.innerHTML = '<div class="loading">No configured miners found.</div>';
    } catch (error) {
        list.innerHTML = '';
        showMessage(error.message, 'error');
    }
}

addForm.addEventListener('submit', addMiner);
bindCapabilities(addForm);
scanButton.addEventListener('click', scanMiners);
loadMiners();
