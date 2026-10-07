// Only Google's managed codeExecution tool runs generated Python.
// REST keeps the response byte limit enforceable before JSON parsing.
export const FLASH_MODEL = 'gemini-3.8-flash';
export const LIMITS = Object.freeze({
    taskChars: 8000, timeoutMs: 60000, responseBytes: 6 * 1024 * 1024,
    textChars: 32000, imageBytes: 2 * 1024 * 1024, images: 4, parts: 128,
    concurrent: 2, perTurn: 3, perSession: 12, receivedCalls: 64, executions: 6,
});
export const EXECUTION_FUNCTION = Object.freeze({
    name: 'execute_python_task',
    behavior: 'BLOCKING',
    description: 'Delegate calculations, data analysis, or matplotlib plotting to Gemini Flash ' +
        'using Google-managed Python execution. Supply a self-contained task with all needed data. ' +
        'Never include credentials. Results and plots appear in the page. Do not retry failed tasks automatically.',
    parameters: {
        type: 'OBJECT', properties: {
            task: {type: 'STRING', description: 'Calculation or plotting task, including relevant data (maximum 8000 characters).'},
        }, required: ['task'],
    },
});

class DelegationError extends Error {
    constructor(code, message) { super(message); this.code = code; }
}
const failure = (code, error) => ({ok: false, code, error});

function httpError(status) {
    const advice = {
        400: 'Check Flash model availability and the documented low thinking / codeExecution configuration.',
        401: 'Check your Gemini API key in Google AI Studio.',
        403: 'Check API key restrictions and permission to use Gemini Flash in this project.',
        404: 'Check access to gemini-3.8-flash and the generateContent API in Google AI Studio.',
        429: 'Check Gemini quota or billing, then try again later.',
    };
    return new DelegationError(`HTTP_${status}`, `Flash request failed (HTTP ${status}). ` +
        (advice[status] || 'Gemini may be temporarily unavailable; try again later.') + ' Your key was kept.');
}

async function readBoundedJSON(response, signal, maxBytes) {
    if (Number(response.headers.get('content-length')) > maxBytes) {
        await response.body?.cancel();
        throw new DelegationError('OUTPUT_LIMIT', 'Flash response exceeded the byte limit. Ask for a smaller result.');
    }
    if (!response.body) throw new DelegationError('EMPTY', 'Flash returned no response body.');
    const reader = response.body.getReader();
    const chunks = [];
    let bytes = 0;
    const cancel = () => { void reader.cancel().catch(() => {}); };
    signal.addEventListener('abort', cancel, {once: true});
    try {
        while (true) {
            if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
            const {value, done} = await reader.read();
            if (done) break;
            bytes += value.byteLength;
            if (bytes > maxBytes) {
                await reader.cancel();
                throw new DelegationError('OUTPUT_LIMIT', 'Flash response exceeded the byte limit. Ask for a smaller result.');
            }
            chunks.push(value);
        }
        if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
        const buffer = new Uint8Array(bytes);
        let offset = 0;
        for (const chunk of chunks) { buffer.set(chunk, offset); offset += chunk.byteLength; }
        try { return JSON.parse(new TextDecoder().decode(buffer)); }
        catch { throw new DelegationError('INVALID_RESPONSE', 'Flash returned invalid JSON. Try again later.'); }
    } finally {
        signal.removeEventListener('abort', cancel);
        reader.releaseLock();
    }
}

export function parseExecutionResponse(response, limits = LIMITS) {
    const items = [];
    let chars = 0, images = 0, parts = 0, executions = 0, lastOutcome = null;
    const addText = (type, value, extra = {}) => {
        if (typeof value !== 'string') return;
        chars += value.length;
        if (chars > limits.textChars) throw new DelegationError('OUTPUT_LIMIT', 'Flash text/code exceeded the output limit. Ask for a smaller result.');
        items.push({type, text: value, ...extra});
    };
    for (const candidate of response.candidates || []) {
        for (const part of candidate.content?.parts || []) {
            if (++parts > limits.parts) throw new DelegationError('OUTPUT_LIMIT', 'Flash returned too many response parts.');
            if (part.thought) continue;
            if (part.text) addText('text', part.text);
            if (part.executableCode) addText('code', part.executableCode.code);
            if (part.codeExecutionResult) {
                executions++;
                if (executions > limits.executions) throw new DelegationError('OUTPUT_LIMIT', 'Flash exceeded the managed execution result limit.');
                lastOutcome = part.codeExecutionResult.outcome;
                addText('result', part.codeExecutionResult.output || '', {outcome: lastOutcome});
            }
            if (part.inlineData) {
                const {mimeType, data} = part.inlineData;
                if (typeof data === 'string' && data.length * 0.75 > limits.imageBytes) {
                    throw new DelegationError('OUTPUT_LIMIT', 'Flash plots exceeded the image limit. Ask for fewer or smaller plots.');
                }
                if (!['image/png', 'image/jpeg', 'image/webp'].includes(mimeType) ||
                    typeof data !== 'string' || !data.length || data.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(data)) {
                    throw new DelegationError('UNSAFE_IMAGE', 'Flash returned an unsupported or malformed image. Only PNG, JPEG, and WebP plots are displayed.');
                }
                if (++images > limits.images || data.length * 0.75 > limits.imageBytes) {
                    throw new DelegationError('OUTPUT_LIMIT', 'Flash plots exceeded the image limit. Ask for fewer or smaller plots.');
                }
                items.push({type: 'image', mimeType, data});
            }
        }
    }
    let error = null;
    if (response.promptFeedback?.blockReason) error = failure('BLOCKED', 'Gemini blocked this execution task. Rephrase the request.');
    else if ((response.candidates || []).some(c => c.finishReason && c.finishReason !== 'STOP')) {
        error = failure('INCOMPLETE', 'Flash did not finish the task (safety or output limit). Ask for a smaller or rephrased task.');
    } else if (!executions || !items.some(item => item.type === 'code')) {
        error = failure('NO_EXECUTION', 'Flash returned no managed Python execution. No calculation was verified; explicitly ask to calculate or plot with Python.');
    } else if (lastOutcome !== 'OUTCOME_OK') {
        error = failure('EXECUTION_FAILED', 'Managed Python execution failed or timed out. Check the displayed result and simplify the task.');
    }
    return {
        items,
        response: error || {ok: true, model: FLASH_MODEL, executions, plots: images,
            // Images stay in the UI; do not put base64 payloads in Live function responses.
            result: items.filter(i => i.type === 'text' || i.type === 'result')
                .map(i => i.text).join('\n').slice(0, limits.textChars)},
    };
}

export async function executeTask(apiKey, task, signal, fetchImpl = fetch, limits = LIMITS) {
    const response = await fetchImpl(`https://generativelanguage.googleapis.com/v1beta/models/${FLASH_MODEL}:generateContent`, {
        method: 'POST', signal, credentials: 'omit', referrerPolicy: 'no-referrer',
        headers: {'Content-Type': 'application/json', 'x-goog-api-key': apiKey},
        body: JSON.stringify({
            systemInstruction: {parts: [{text: 'Use the managed codeExecution tool to generate and run Python for the task. ' +
                'For plots use matplotlib and return inline plot images. Print useful results, then summarize them. ' +
                'Keep output concise. Never claim execution if the tool did not run. Do not request other tools.'}]},
            contents: [{role: 'user', parts: [{text: task}]}],
            tools: [{codeExecution: {}}],
            generationConfig: {thinkingConfig: {thinkingLevel: 'low'}, maxOutputTokens: 8192, candidateCount: 1},
        }),
    });
    if (!response.ok) { await response.body?.cancel(); throw httpError(response.status); }
    return parseExecutionResponse(await readBoundedJSON(response, signal, limits.responseBytes), limits);
}

export class ExecutionDelegate {
    constructor({apiKey, send, display, fatal, fetchImpl = fetch, limits = LIMITS}) {
        Object.assign(this, {apiKey, send, display, fatal, fetchImpl, limits});
        this.active = new Map();
        this.seen = new Set();
        this.total = 0;
        this.turnCalls = 0;
        this.received = 0;
        this.closed = false;
    }
    newUserTurn() { this.turnCalls = 0; }
    cancel(ids) { for (const id of ids) this.active.get(id)?.abort(); }
    close() { this.closed = true; for (const controller of this.active.values()) controller.abort(); }
    async handle(calls = []) {
        if (this.closed) return;
        if (!Array.isArray(calls)) {
            this.close();
            this.fatal('Live sent malformed function calls. Stop and Start to reconnect.');
            return;
        }
        this.received += calls.length;
        if (this.received > this.limits.receivedCalls) {
            this.close();
            this.fatal('Too many Live tool calls. Listening stopped; Start again to continue.');
            return;
        }
        await Promise.all(calls.map(call => this.handleCall(call)));
    }
    async handleCall(call) {
        if (this.closed) return;
        if (!call || typeof call.id !== 'string' || !call.id || call.id.length > 256 ||
            typeof call.name !== 'string' || call.name.length > 128) {
            this.fatal('Live sent an invalid function call ID or name. Stop and Start to reconnect.');
            return;
        }
        if (this.seen.has(call.id)) return;
        this.seen.add(call.id);
        let result, items = [], controller, timer;
        // Reserve UI/history position before awaiting the asynchronous task.
        const display = this.display(call);
        const task = call.args?.task;
        if (call.name !== EXECUTION_FUNCTION.name) result = failure('UNKNOWN_FUNCTION', 'Unknown execution function.');
        else if (typeof task !== 'string' || !task.trim() || task.length > this.limits.taskChars) {
            result = failure('INVALID_TASK', `Supply a nonempty, self-contained task up to ${this.limits.taskChars} characters.`);
        } else if (this.total >= this.limits.perSession || this.turnCalls >= this.limits.perTurn) {
            result = failure('CALL_LIMIT', 'Execution call limit reached. Send a new task, or Stop/Start if the session limit was reached.');
        } else if (this.active.size >= this.limits.concurrent) result = failure('BUSY', 'Two execution tasks are already running. Wait before trying again.');
        else {
            this.total++; this.turnCalls++;
            controller = new AbortController();
            this.active.set(call.id, controller);
            let timedOut = false;
            timer = setTimeout(() => { timedOut = true; controller.abort(); }, this.limits.timeoutMs);
            try {
                const output = await executeTask(this.apiKey, task, controller.signal, this.fetchImpl, this.limits);
                items = output.items; result = output.response;
            } catch (error) {
                if (controller.signal.aborted && !timedOut) {
                    display([], failure('CANCELLED', 'Execution cancelled. No result was sent to Live.'));
                    return;
                }
                result = timedOut ? failure('TIMEOUT', 'Flash exceeded the request deadline. Simplify the task and try again.') :
                    failure(error.code || 'NETWORK', error.code ? error.message :
                        'Could not reach Gemini Flash. Check your connection, browser CORS/network policy, and API key restrictions. Your key was kept.');
            } finally {
                clearTimeout(timer);
                this.active.delete(call.id);
            }
        }
        if (this.closed || controller?.signal.aborted && result?.code !== 'TIMEOUT') return;
        display(items, result);
        // Preserve the original call ID and name on successes and failures.
        try { this.send({functionResponses: [{id: call.id, name: call.name, response: result}]}); }
        catch { this.fatal('Could not return the execution result to Live. Stop and Start to reconnect.'); }
    }
}
