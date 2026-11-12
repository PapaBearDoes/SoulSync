// Tests for the Shelf Life chip on library cards (`webui/static/video/video-library.js`).
//
//     node --test tests/static/test_shelf_life_badge.mjs
//
// the server only sends shelf_life (and the threshold) to admins; the chip is
// the score in a colour band against the job's threshold, a shield for a gated
// title, and nothing for a card the job never scored.

import { test, describe, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(
    resolve(__dirname, '..', '..', 'webui', 'static', 'video', 'video-library.js'), 'utf8');

function lift(name) {
    const start = SOURCE.indexOf(`function ${name}(`);
    assert.ok(start !== -1, `${name} not found in video-library.js`);
    let i = SOURCE.indexOf('{', start);
    let depth = 0;
    for (; i < SOURCE.length; i++) {
        if (SOURCE[i] === '{') depth++;
        else if (SOURCE[i] === '}') { depth--; if (depth === 0) { i++; break; } }
    }
    return SOURCE.slice(start, i);
}

let sb;
beforeEach(() => {
    sb = { state: { slThreshold: 30 } };
    vm.createContext(sb);
    vm.runInContext([lift('esc'), lift('shelfLifeBadge')].join('\n'), sb);
});

const band = (html) => (html.match(/video-card-shelf--(\w+)/) || [])[1];

describe('shelfLifeBadge', () => {
    test('nothing when the card was never scored', () => {
        assert.equal(sb.shelfLifeBadge(undefined), '');
        assert.equal(sb.shelfLifeBadge(null), '');
        assert.equal(sb.shelfLifeBadge({ score: null, gate: null }), '');
    });

    test('below the threshold is red and says it is flagged', () => {
        const html = sb.shelfLifeBadge({ score: 12 });
        assert.equal(band(html), 'low');
        assert.match(html, />12</);
        assert.match(html, /flagged for cleanup/);
    });

    test('the threshold itself is not flagged', () => {
        assert.equal(band(sb.shelfLifeBadge({ score: 30 })), 'mid');
    });

    test('amber runs 15 points above the line, green after', () => {
        assert.equal(band(sb.shelfLifeBadge({ score: 44 })), 'mid');
        assert.equal(band(sb.shelfLifeBadge({ score: 45 })), 'ok');
    });

    test('the bands follow the job threshold', () => {
        sb.state.slThreshold = 50;
        assert.equal(band(sb.shelfLifeBadge({ score: 45 })), 'low');
    });

    test('a gated title gets a shield with the gate on hover, escaped', () => {
        const html = sb.shelfLifeBadge({ score: null, gate: 'keep', gate_text: 'Kept <by hand>' });
        assert.equal(band(html), 'gate');
        assert.match(html, /Kept &lt;by hand&gt;/);
        assert.doesNotMatch(html, />\d+</);
    });
});
