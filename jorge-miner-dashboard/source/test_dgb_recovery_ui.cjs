const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

const script = fs.readFileSync(path.join(__dirname, 'static/dashboard.js'), 'utf8')
    .split("const swipe = el('swipeContainer');")[0];

function settingUI(responses) {
    const control = {checked: false, disabled: false, indeterminate: false};
    const state = {textContent: ''};
    const calls = [];
    let change;
    control.addEventListener = (_, listener) => { change = listener; };
    const document = {getElementById: id => id === 'dgbRecoveryEnabled' ? control : state};
    const fetch = async (url, options = {}) => {
        calls.push([url, options.method || 'GET']);
        const next = responses.shift();
        if (next instanceof Error) throw next;
        return {ok: next.ok ?? true, json: async () => next.data};
    };
    const context = vm.createContext({document, fetch});
    vm.runInContext(script, context);
    return {control, state, calls, change: () => change({currentTarget: control})};
}

for (const enabled of [true, false]) {
    test(`failed POST reloads authoritative ${enabled ? 'Enabled' : 'Disabled'} setting`, async () => {
        const ui = settingUI([new Error('response lost'), {data: {enabled}}]);
        ui.control.checked = !enabled;
        await ui.change();
        assert.deepEqual(ui.calls.map(call => call[1]), ['POST', 'GET']);
        assert.equal(ui.control.checked, enabled);
        assert.equal(ui.control.disabled, false);
        assert.equal(ui.control.indeterminate, false);
        assert.equal(ui.state.textContent, enabled ? 'Enabled' : 'Disabled');
    });
}

test('failed POST and failed GET leave state unknown', async () => {
    const ui = settingUI([new Error('response lost'), new Error('reload failed')]);
    ui.control.checked = true;
    await ui.change();
    assert.deepEqual(ui.calls.map(call => call[1]), ['POST', 'GET']);
    assert.equal(ui.control.disabled, true);
    assert.equal(ui.control.indeterminate, true);
    assert.equal(ui.state.textContent, 'Unknown');
});

test('successful POST displays server returned state', async () => {
    const ui = settingUI([{data: {enabled: false}}]);
    ui.control.checked = true;
    await ui.change();
    assert.deepEqual(ui.calls.map(call => call[1]), ['POST']);
    assert.equal(ui.control.checked, false);
    assert.equal(ui.state.textContent, 'Disabled');
});
