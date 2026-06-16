const list = document.getElementById('minerList');
const message = document.getElementById('message');
const addForm = document.getElementById('addMinerForm');

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
    return {
        original_name: data.original_name,
        name: data.name,
        ip: data.ip,
        type: data.type,
        pool: data.pool || '',
        coin: data.coin || '',
    };
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
            <div class="settings-grid">
                ${textInput('name', 'Miner name', miner.name, 'maxlength="48" required')}
                ${textInput('ip', 'IP address', miner.ip, 'inputmode="decimal" required')}
                ${osSelect(miner.type)}
                ${textInput('pool', 'Pool', miner.pool, 'maxlength="64"')}
                ${textInput('coin', 'Coin', miner.coin, 'maxlength="16"')}
            </div>
            <div class="form-actions">
                <span class="save-note">Thermal settings stay on the Thermal Settings page.</span>
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
        showMessage(`${payload.name} added. Thermal management is disabled.`, 'success');
        await loadMiners();
    } catch (error) {
        showMessage(error.message, 'error');
    } finally {
        button.disabled = false;
        button.textContent = 'Add Miner';
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
        data.miners.forEach(miner => list.appendChild(renderMiner(miner)));
        if (!data.miners.length) list.innerHTML = '<div class="loading">No configured miners found.</div>';
    } catch (error) {
        list.innerHTML = '';
        showMessage(error.message, 'error');
    }
}

addForm.addEventListener('submit', addMiner);
loadMiners();
