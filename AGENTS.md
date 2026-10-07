# Repository guide

## Structure

- `main.py` is the Flask server. It serves `landing.html` until the browser has a `gemini_api_key` cookie, then serves `gemini-live.html`. It also serves the favicon, screenshot, and robots file.
- `gemini-live.html` contains the browser UI, microphone worklets, Gemini Live session, Markdown/LaTeX rendering, and conversation history.
- `static/delegation.js` implements direct browser REST delegation to Gemini Flash and bounded response parsing. Flask serves it through its existing static route; do not add a Python execution endpoint.
- `requirements.txt` lists Python dependencies; there is no dependency lockfile or frontend build step. Browser libraries load from CDNs.
- `cloudbuild.yaml` is the deployment configuration at the repository root. The obsolete `.idx/` directory has been removed.
- `tests/test_live_browser.py` contains Chromium regression tests using the real GenAI SDK and simulated Gemini WebSocket responses.
- `tests/test_delegation.mjs` contains Node regressions using simulated Flash responses, including limits and cancellation.

Use the existing checkout. Cloud tasks already run in isolated environments; do not create a Git worktree unless the user asks. Preserve existing user changes.

## Development

Run from the repository root:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python -m flask --app main run --host 127.0.0.1 --port 5000
```

Use the Flask command directly: `devserver.sh` assumes a `PORT` variable and uses `source` under `/bin/sh`, which is not portable. Microphone access requires an HTTPS or localhost secure context. Do not present localhost URLs as cloud onboarding preview links.

The server does not need a Gemini credential in its environment. Users enter their own key through the browser form. Never print, commit, or embed real API keys in source, tests, logs, or setup instructions. Connection failures must not delete a user's key merely because a model or configuration is unavailable.

## Gemini 3.8 Live requirements

- The model is `gemini-3.8-live`; the GenAI SDK is pinned to `2.27.0` through jsDelivr.
- Native-audio models require `responseModalities: [Modality.AUDIO]`. Requesting `TEXT` caused the reported WebSocket close code 1007.
- Enable `inputAudioTranscription` and `outputAudioTranscription`. Display output transcription incrementally and discard returned audio; this app intentionally provides silent text responses.
- Put `systemInstruction` inside the session's `config` object.
- Keep Google Search and the custom `execute_python_task` function. Live does not support built-in code execution or image generation; delegated matplotlib plots come from Flash's managed execution.
- Do not set `thinkingConfig` for `gemini-3.8-live`, disable proactive audio, or reintroduce removed affective-dialogue settings.
- Send microphone PCM at 16 kHz. Connect recording and volume worklets to a muted destination so the browser keeps processing the graph without microphone feedback.
- Wait for `serverContent.turnComplete` before saving a completed model turn: `generationComplete` can arrive before the final transcription chunks. Ignore thought text when rendering model text parts.
- Explicit user/model roles are supported by `sendClientContent`. Setting `turnComplete: true` interrupts active generation, so preserve chronological history when adding text, images, or voice input.
- Reserve history positions when input/model streaming begins and update records in place. Never append an interrupted model answer after the user turn that interrupted it. Keep input transcription open while rendering output, until `inputTranscription.finished` or a turn boundary; input and output transcription are independent.
- `interrupted` is not the model-record closing boundary: keep the reserved record through `turnComplete`, consuming tail chunks in the interruption frame and subsequent frames. The SDK documents `interrupted` followed by `turnComplete`; test fixtures must include that boundary before the next model response.
- `interrupted` is not an input-transcription boundary either. Keep a voice utterance's record and per-turn execution budget across interruption; later chunks update the same record. Close input on `inputTranscription.finished` or `turnComplete` (or explicit user send/Stop cleanup), not the interruption notification.
- Sanitize Markdown with DOMPurify. Render execution code/results with `textContent`, and allow only bounded PNG/JPEG/WebP inline plots. Do not insert model-generated HTML directly.

## Delegated Python requirements

- `gemini-3.8-flash` handles self-contained execution tasks with `tools: [{codeExecution: {}}]` and `generationConfig.thinkingConfig.thinkingLevel: 'low'`. `minimal` is unsupported. Keep Live as `gemini-3.8-live`, with no Live thinking configuration.
- Use direct browser `generateContent` REST with the existing user key in `x-goog-api-key`, not the URL. The bounded body reader is intentional: an SDK convenience call would buffer JSON before the app's byte limit. Never print keys or raw remote error bodies. Error rendering redacts the current key.
- Python runs exclusively in Google's managed tool. Never use `eval`, Flask execution, browser Python runtimes, or local subprocesses to run generated code. Test subprocesses start Flask/Node only.
- Handle all response parts, including multiple text and images; omit thoughts. Check execution outcomes and incomplete/blocked responses. A text-only answer is not verified execution.
- Return function responses with the original call ID and name, including failures. Deduplicate IDs; cancel pending tasks on Live cancellation or Stop and suppress stale results. SDK declaration conversion mutates its input: pass a mutable copy of `EXECUTION_FUNCTION`.
- Preserve the limits in `LIMITS`: 2 concurrent, 3 per user turn, 12 per session, 64 received calls, 8000 task characters, 60 seconds, 6 MiB response body, 32,000 text/code characters, 128 parts, 4 plots at 2 MiB each, and 6 managed execution results. Requests also cap output at 8192 tokens. No application retries or execution continuation loop.
- Google's service documents a 30-second runtime and up to five code regenerations. Client abort cannot guarantee cancellation of work already running at Google. Do not claim stronger remote execution controls than the API exposes.
- Save descriptive execution summaries at the original history position; keep plots in the UI. Do not replay dangling function IDs or dump image base64 into Live tool responses.
- Consume model content before tool calls in a shared message. Close the current model segment when a new execution call reserves its summary, so replay remains preamble, execution, continuation. Duplicate call IDs must not close segments or create extra summaries.

Official API contracts checked October 6, 2026: [Flash model](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash), [low thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking), [generateContent managed execution](https://ai.google.dev/gemini-api/docs/generate-content/code-execution), [current execution guide and limits](https://ai.google.dev/gemini-api/docs/code-execution), and [Live tool combination/function responses](https://ai.google.dev/gemini-api/docs/live-api/tools). The newer Interactions API is documented separately; do not mix its snake_case step schema with generateContent response parts.

Authoritative references: [model and migration guide](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live), [Live API capabilities](https://ai.google.dev/gemini-api/docs/live-guide), and [tool support](https://ai.google.dev/gemini-api/docs/live-tools).

## Tests and verification limits

The test runner needs Playwright, and the server dependencies must exist in `.venv`. Install Playwright in the Python environment used for the test command. The suite uses system Chromium when available; otherwise install a browser with `python -m playwright install chromium`. `CHROMIUM_PATH` can select another executable.

```sh
node --test --test-reporter=tap tests/test_delegation.mjs
python -m unittest discover -s tests -v
git diff --check
```

The browser suite uses the real SDK, simulated microphone capture, simulated Live responses, and mocked Flash HTTP responses. The Node suite uses simulated responses as well. These tests do not prove a real Gemini connection, Python execution, browser-to-Gemini CORS, or Cloud Run deployment works. Report simulation separately from real validation; an end-to-end test needs a user key authorized for both models, quota/billing, browser network access, and microphone permission. No Gemini credential is configured in this environment.

In the cloud environment, native Chromium CDN requests failed certificate verification, while Python HTTPS with the system CA bundle worked. Tests fetch public CDN resources using verified Python HTTPS and fulfill browser requests with those bytes. Keep TLS verification enabled; do not use insecure browser flags or change persistent certificate trust without explicit authorization. This test path does not validate native Chromium HTTPS through the proxy.

### Addressed PR #3 review findings

[PR #3 review](https://github.com/jsalsman/gemini-live/pull/3#discussion_r4201706141) identified a P2 history-ordering bug on commit `9f796ee`: text/image send paths can record an interrupting user turn before the pending model answer is saved. Voice barge-in can similarly finalize new input before the pending model answer. Stop/Start replay can therefore invert the conversational order.

Latest main (`d0609b8`) still had this bug. Delegation work fixes it by reserving the model record when its first chunk arrives, so later chunks update its original history position even after an interrupting user turn is recorded. Browser tests cover typed interruption with late model chunks, image interruption, voice input arriving before the interruption notification, and Stop/Start replay. The related [late input-transcription review](https://github.com/jsalsman/gemini-live/pull/3#discussion_r4201732200) is addressed by buffering input independently rather than finalizing it during model rendering.

PR #4 follow-up regressions cover interruption-frame tails, tails between `interrupted` and `turnComplete`, preamble/tool/continuation ordering for combined and separate messages, and duplicate call IDs. The record remains open until the documented boundary, and execution summaries split model segments without closing input transcription. See the [SDK server-content contract](https://googleapis.github.io/js-genai/release_docs/interfaces/types.LiveServerContent.html).

## Cloud Run deployment

The Cloud Build trigger runs after merging into `main`, not on the PR branch. Ensure the trigger reads the root `cloudbuild.yaml`; an inline trigger definition will not automatically use it.

The supplied configuration builds with Google Buildpacks, pushes the container to Artifact Registry, and updates Cloud Run service `idx-talknicer-live-57704335` in project `virtual-indexer-344822`, region `us-west1`. Keep the supplied substitutions and destination unless the user requests a change. Cloud Build supplies commit/repository/build substitutions; a local YAML parse alone does not validate a deployment.

After deployment, test the HTTPS application with a real browser key and microphone: start listening, confirm input and output transcription without the modality error, test text/image input and Google Search, then verify Stop/Start retains correctly ordered context. Distinguish local tests, Cloud Build results, and real Gemini validation in reports.

For GitHub operations, reuse the configured platform authentication. A read-only Git success does not prove write access. Earlier Git push and GitHub API writes returned 403 until the integration received repository write permission; publishing PR #3 then succeeded. Never dump tokens or credential files while diagnosing access.
