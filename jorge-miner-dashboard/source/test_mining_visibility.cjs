const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

function dashboard() {
    const nodes = new Map();
    const node = id => {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: false, style: {}, textContent: '', innerHTML: '',
            addEventListener: () => {},
            closest: () => node(id + '-card'),
        });
        return nodes.get(id);
    };
    const context = vm.createContext({document: {getElementById: node, querySelector: node}});
    const script = fs.readFileSync(path.join(__dirname, 'static/dashboard.js'), 'utf8');
    vm.runInContext(script.split("const swipe = el('swipeContainer');")[0], context);
    return {node, render: data => context.renderStrategy(data)};
}

const pools = ['Umbrel Solo', 'BCH SoloPool', 'DGB SHA-256d'];
const prefixes = ['btc', 'bch', 'dgb'];
const ids = ['allocBtc', 'allocBch', 'allocDgb'];
function fixture(mask, offline = false) {
    const data = {miners: [], solo_pools: {}};
    pools.forEach((pool, index) => {
        const assigned = mask & (1 << index) ? 5 : 0;
        const th = assigned && !offline ? 2 : 0;
        data.solo_pools[pool] = {assigned_count: assigned, active_count: th ? assigned : 0, current_hashrate_th: th};
        if (assigned) data.miners.push({name: pool, pool, online: !offline, th});
    });
    data.miners.push({name: 'Braiins miner', pool: 'Braiins', online: true, th: 2});
    return data;
}

for (let index = 0; index < pools.length; index++) {
    test(`${pools[index]} hides without assignments and returns while offline`, () => {
        const {node, render} = dashboard();
        render(fixture(0));
        assert.equal(node(prefixes[index] + 'SoloMiners-card').hidden, true);
        assert.equal(node(ids[index]).hidden, true);
        assert.ok(!node('allocationLegend').innerHTML.includes(prefixes[index] + '-label'));
        render(fixture(1 << index, true));
        assert.equal(node(prefixes[index] + 'SoloMiners-card').hidden, false);
        assert.equal(node(prefixes[index] + 'SoloMiners').textContent, '● 0 OF 5 MINERS WORKING');
        assert.equal(node(ids[index]).hidden, false);
        assert.ok(node('allocationLegend').innerHTML.includes(prefixes[index] + '-label'));
        assert.equal(node(ids[index]).style.width, '0%');
        assert.equal(node('allocBraiins').style.width, '100%');
    });
}

test('all assignment combinations keep correct percentages and collapse empty solo grid', () => {
    const {node, render} = dashboard();
    for (let mask = 0; mask < 8; mask++) {
        render(fixture(mask));
        const count = prefixes.filter((_, index) => mask & (1 << index)).length;
        assert.equal(node('.solo-grid').hidden, count === 0);
        for (let index = 0; index < 3; index++) {
            const assigned = Boolean(mask & (1 << index));
            assert.equal(node(ids[index]).hidden, !assigned);
            assert.equal(node(prefixes[index] + 'SoloMiners-card').hidden, !assigned);
            if (assigned) assert.ok(Math.abs(parseFloat(node(ids[index]).style.width) - 100 / (count + 1)) < 1e-10);
        }
        assert.ok(Math.abs(parseFloat(node('allocBraiins').style.width) - 100 / (count + 1)) < 1e-10);
        assert.equal((node('allocationLegend').innerHTML.match(/<span/g) || []).length, count + 1);
    }
});

test('Braiins allocation hides without assignments; its section renderer still runs', () => {
    const {node, render} = dashboard();
    const data = fixture(4);
    data.miners = data.miners.filter(miner => miner.pool !== 'Braiins');
    render(data);
    assert.equal(node('allocBraiins').hidden, true);
    assert.ok(!node('allocationLegend').innerHTML.includes('braiins-label'));
    assert.equal(node('allocDgb').style.width, '100%');
    assert.equal(node('braiinsWorkerList').innerHTML, 'Braiins API unavailable or no workers');
});
