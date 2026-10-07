# Repository guide

## Structure

- `main.py` is the Flask server. It serves `landing.html` until the browser has a `gemini_api_key` cookie, then serves `gemini-live.html`. It also serves the favicon, screenshot, and robots file.
- `gemini-live.html` contains the browser UI, microphone worklets, Gemini Live session, Markdown/LaTeX rendering, and conversation history.
- `requirements.txt` lists Python dependencies; there is no dependency lockfile or frontend build step. Browser libraries load from CDNs.
- `cloudbuild.yaml` is the deployment configuration at the repository root. The obsolete `.idx/` directory has been removed.
- `tests/test_live_browser.py` contains Chromium regression tests using the real GenAI SDK and simulated Gemini WebSocket responses.

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
- Google Search is supported. Built-in code execution and generated images are not supported by this model; do not advertise or request those tools.
- Do not set `thinkingConfig` for `gemini-3.8-live`, disable proactive audio, or reintroduce removed affective-dialogue settings.
- Send microphone PCM at 16 kHz. Connect recording and volume worklets to a muted destination so the browser keeps processing the graph without microphone feedback.
- Wait for `serverContent.turnComplete` before saving a completed model turn: `generationComplete` can arrive before the final transcription chunks. Ignore thought text when rendering model text parts.
- Explicit user/model roles are supported by `sendClientContent`. Setting `turnComplete: true` interrupts active generation, so preserve chronological history when adding text, images, or voice input.

Authoritative references: [model and migration guide](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live), [Live API capabilities](https://ai.google.dev/gemini-api/docs/live-guide), and [tool support](https://ai.google.dev/gemini-api/docs/live-tools).

## Tests and verification limits

The test runner needs Playwright, and the server dependencies must exist in `.venv`. Install Playwright in the Python environment used for the test command. The suite uses system Chromium when available; otherwise install a browser with `python -m playwright install chromium`. `CHROMIUM_PATH` can select another executable.

```sh
python -m unittest discover -s tests -v
git diff --check
```

All five existing browser tests passed on the initial Gemini 3.8 change. They verify session configuration, microphone-worklet PCM delivery with simulated capture, streaming transcripts, interruption rendering, restart context, and error cleanup. They use simulated server responses and do not prove a real Gemini connection, tool execution, or a Cloud Run deployment works.

In the cloud environment, native Chromium CDN requests failed certificate verification, while Python HTTPS with the system CA bundle worked. Tests fetch public CDN resources using verified Python HTTPS and fulfill browser requests with those bytes. Keep TLS verification enabled; do not use insecure browser flags or change persistent certificate trust without explicit authorization. This test path does not validate native Chromium HTTPS through the proxy.

### Open review finding

[PR #3 review](https://github.com/jsalsman/gemini-live/pull/3#discussion_r4201706141) identified a P2 history-ordering bug on commit `9f796ee`: text/image send paths can record an interrupting user turn before the pending model answer is saved. Voice barge-in can similarly finalize new input before the pending model answer. Stop/Start replay can therefore invert the conversational order.

This finding is not yet fixed. Preserve the pending model turn before the interrupting user turn, including asynchronous interruption events, and add regression coverage for replay ordering when addressing it. The current interruption-rendering test does not cover this scenario.

## Cloud Run deployment

The Cloud Build trigger runs after merging into `main`, not on the PR branch. Ensure the trigger reads the root `cloudbuild.yaml`; an inline trigger definition will not automatically use it.

The supplied configuration builds with Google Buildpacks, pushes the container to Artifact Registry, and updates Cloud Run service `idx-talknicer-live-57704335` in project `virtual-indexer-344822`, region `us-west1`. Keep the supplied substitutions and destination unless the user requests a change. Cloud Build supplies commit/repository/build substitutions; a local YAML parse alone does not validate a deployment.

After deployment, test the HTTPS application with a real browser key and microphone: start listening, confirm input and output transcription without the modality error, test text/image input and Google Search, then verify Stop/Start retains correctly ordered context. Distinguish local tests, Cloud Build results, and real Gemini validation in reports.

For GitHub operations, reuse the configured platform authentication. A read-only Git success does not prove write access. Earlier Git push and GitHub API writes returned 403 until the integration received repository write permission; publishing PR #3 then succeeded. Never dump tokens or credential files while diagnosing access.
