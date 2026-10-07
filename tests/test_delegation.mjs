import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
// Load the browser ES module without introducing a frontend package/build step.
const source = await readFile(new URL('../delegation.js', import.meta.url));
const {ExecutionDelegate, executeTask, parseExecutionResponse, LIMITS} =
    await import(`data:text/javascript;base64,${source.toString('base64')}`);

const parts = [
    {text: 'Calculating '}, {text: 'with Python.'},
    {executableCode: {language: 'PYTHON', code: 'print(2 + 2)'}},
    {codeExecutionResult: {outcome: 'OUTCOME_OK', output: '4'}},
    {inlineData: {mimeType: 'image/png', data: 'AAAA'}},
    {text: 'Two plots.'}, {inlineData: {mimeType: 'image/webp', data: 'AAAA'}},
];
const response = (p = parts, finishReason = 'STOP') => ({candidates: [{content: {parts: p}, finishReason}]});
const okFetch = async () => Response.json(response());
const call = (id = 'call-1', task = 'Calculate 2 + 2') => ({id, name: 'execute_python_task', args: {task}});
function harness(options = {}) {
    const sent = [], displays = [], errors = [];
    const delegate = new ExecutionDelegate({apiKey: 'unit-test-placeholder',
        send: message => sent.push(message), display: c => (items, result) => displays.push({c, items, result}),
        fatal: error => errors.push(error), fetchImpl: okFetch, ...options});
    return {delegate, sent, displays, errors};
}

test('direct request pins model, low reasoning, managed execution and output tokens', async () => {
    let request;
    const result = await executeTask('unit-test-placeholder', 'Plot x*x', new AbortController().signal,
        async (url, config) => { request = {url, config}; return Response.json(response()); });
    assert.match(request.url, /gemini-3\.8-flash:generateContent$/);
    assert.ok(!request.url.includes('placeholder'));
    assert.equal(request.config.headers['x-goog-api-key'], 'unit-test-placeholder');
    assert.equal(request.config.credentials, 'omit');
    const body = JSON.parse(request.config.body);
    assert.deepEqual(body.tools, [{codeExecution: {}}]);
    assert.deepEqual(body.generationConfig, {thinkingConfig: {thinkingLevel: 'low'}, maxOutputTokens: 8192, candidateCount: 1});
    assert.equal(body.contents[0].parts[0].text, 'Plot x*x');
    assert.equal(result.response.ok, true);
});

test('all text/code/result/image parts are processed, thoughts excluded, plots excluded from function payload', () => {
    const result = parseExecutionResponse(response([{text: 'private', thought: true}, ...parts]));
    assert.deepEqual(result.items.map(i => i.type), ['text', 'text', 'code', 'result', 'image', 'text', 'image']);
    assert.equal(result.response.plots, 2);
    assert.match(result.response.result, /4/);
    assert.ok(!JSON.stringify(result.response).includes('AAAA'));
    assert.ok(!JSON.stringify(result).includes('private'));
});

test('multiple candidates and recovered managed executions retain their results', () => {
    const result = parseExecutionResponse({candidates: [
        {content: {parts: [{codeExecutionResult: {outcome: 'OUTCOME_FAILED', output: 'first attempt failed'}}, ...parts]}},
        {content: {parts: [{text: 'additional part'}]}},
    ]});
    assert.equal(result.response.executions, 2);
    assert.equal(result.response.ok, true);
    assert.match(result.response.result, /first attempt failed[\s\S]*additional part/);
});

test('execution failure, blocked, text-only and truncated output never claim success', () => {
    for (const [payload, code] of [
        [response([{executableCode: {code: '1/0'}}, {codeExecutionResult: {outcome: 'OUTCOME_FAILED', output: 'division by zero'}}]), 'EXECUTION_FAILED'],
        [{promptFeedback: {blockReason: 'SAFETY'}}, 'BLOCKED'],
        [response([{text: '4'}]), 'NO_EXECUTION'],
        [response(parts, 'MAX_TOKENS'), 'INCOMPLETE'],
        [response([]), 'NO_EXECUTION'],
    ]) {
        const result = parseExecutionResponse(payload);
        assert.equal(result.response.ok, false);
        assert.equal(result.response.code, code);
    }
});

test('unsafe image MIME/base64 and all output budgets fail closed', () => {
    for (const image of [
        {mimeType: 'image/svg+xml', data: 'AAAA'}, {mimeType: 'text/html', data: 'AAAA'},
        {mimeType: 'image/png', data: '<script>'},
    ]) assert.throws(() => parseExecutionResponse(response([{inlineData: image}])), /unsupported or malformed/);
    assert.throws(() => parseExecutionResponse(response(), {...LIMITS, images: 1}), /image limit/);
    assert.throws(() => parseExecutionResponse(response(), {...LIMITS, imageBytes: 1}), /image limit/);
    assert.throws(() => parseExecutionResponse(response(), {...LIMITS, textChars: 1}), /output limit/);
    assert.throws(() => parseExecutionResponse(response(), {...LIMITS, parts: 2}), /too many/);
    assert.throws(() => parseExecutionResponse(response(), {...LIMITS, executions: 0}), /execution result limit/);
});

test('chunked HTTP output is bounded before JSON parsing and cancelled', async () => {
    let cancelled = false;
    const fetchImpl = async () => new Response(new ReadableStream({
        start(controller) { controller.enqueue(new Uint8Array(10)); },
        cancel() { cancelled = true; },
    }));
    await assert.rejects(executeTask('unit-test-placeholder', 'calculate', new AbortController().signal,
        fetchImpl, {...LIMITS, responseBytes: 2}), /byte limit/);
    assert.equal(cancelled, true);
});

test('batch calls return original IDs/names and duplicate IDs execute once', async () => {
    let fetches = 0;
    const h = harness({fetchImpl: async () => { fetches++; return okFetch(); }});
    await h.delegate.handle([call('a'), call('b')]);
    await h.delegate.handle([call('a')]);
    assert.equal(fetches, 2);
    assert.deepEqual(h.sent.map(m => m.functionResponses[0].id).sort(), ['a', 'b']);
    assert.ok(h.sent.every(m => m.functionResponses[0].name === 'execute_python_task'));
});

test('unknown/malformed task and turn/session budgets return errors without requests', async () => {
    let fetches = 0;
    const h = harness({fetchImpl: async () => { fetches++; return okFetch(); },
        limits: {...LIMITS, perTurn: 1, perSession: 2}});
    await h.delegate.handle([{...call('unknown'), name: 'unknown'}, call('bad', ''), call('large', 'x'.repeat(8001))]);
    assert.equal(fetches, 0);
    await h.delegate.handle([call('first'), call('limited')]);
    assert.equal(fetches, 1);
    h.delegate.newUserTurn();
    await h.delegate.handle([call('second')]);
    h.delegate.newUserTurn();
    await h.delegate.handle([call('session-limit')]);
    assert.equal(fetches, 2);
    assert.equal(h.sent.at(-1).functionResponses[0].response.code, 'CALL_LIMIT');
});

test('all HTTP/model failures return actionable errors without leaking remote error bodies or retrying', async () => {
    for (const status of [400, 401, 403, 404, 429, 500]) {
        let fetches = 0;
        const h = harness({fetchImpl: async () => {
            fetches++; return new Response('remote body unit-test-placeholder', {status});
        }});
        await h.delegate.handle([call()]);
        const result = h.sent[0].functionResponses[0];
        assert.equal(result.id, 'call-1');
        assert.equal(result.response.code, `HTTP_${status}`);
        assert.match(result.response.error, /Your key was kept/);
        assert.ok(!JSON.stringify(h.sent).includes('placeholder'));
        assert.equal(fetches, 1);
    }
});

test('timeouts return matched function failures, never retry', async () => {
    const h = harness({limits: {...LIMITS, timeoutMs: 5}, fetchImpl: async (_url, {signal}) =>
        new Promise((resolve, reject) => signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))});
    await h.delegate.handle([call()]);
    assert.equal(h.sent[0].functionResponses[0].response.code, 'TIMEOUT');
});

test('deadline also cancels a stalled response body after HTTP headers arrive', async () => {
    let cancelled = false;
    const h = harness({limits: {...LIMITS, timeoutMs: 5}, fetchImpl: async () => new Response(new ReadableStream({
        start(controller) { controller.enqueue(new TextEncoder().encode('{')); },
        cancel() { cancelled = true; },
    }))});
    await h.delegate.handle([call()]);
    assert.equal(h.sent[0].functionResponses[0].response.code, 'TIMEOUT');
    assert.equal(cancelled, true);
});

test('concurrency is bounded; cancellation/Stop suppress stale results and function responses', async () => {
    const h = harness({fetchImpl: async (_url, {signal}) => new Promise((resolve, reject) =>
        signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))))});
    const pending = h.delegate.handle([call('a'), call('b'), call('busy')]);
    assert.equal(h.delegate.active.size, 2);
    assert.equal(h.sent[0].functionResponses[0].response.code, 'BUSY');
    h.delegate.cancel(['a']);
    h.delegate.close();
    await pending;
    assert.equal(h.delegate.active.size, 0);
    assert.equal(h.sent.length, 1);
    await h.delegate.handle([call('stopped')]);
    assert.equal(h.sent.length, 1);
});

test('missing IDs and excessive incoming calls cannot grow state without limit', async () => {
    const h = harness({limits: {...LIMITS, receivedCalls: 1}});
    await h.delegate.handle([{...call(), id: null}]);
    assert.equal(h.errors.length, 1);
    assert.equal(h.sent.length, 0);
    await h.delegate.handle([call()]);
    assert.equal(h.delegate.closed, true);
    assert.equal(h.errors.length, 2);
});

test('network and invalid JSON errors return bounded actionable failure payloads', async () => {
    for (const fetchImpl of [async () => { throw Error('secret remote detail'); }, async () => new Response('not JSON')]) {
        const h = harness({fetchImpl});
        await h.delegate.handle([call()]);
        assert.equal(h.sent[0].functionResponses[0].response.ok, false);
        assert.ok(!JSON.stringify(h.sent).includes('secret remote detail'));
    }
});
