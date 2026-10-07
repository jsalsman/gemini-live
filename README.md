# Gemini Live Voice to Text Realtime Stream

[![Run on Google Cloud Run](https://img.shields.io/badge/JavaScript-Run_in_browser-brightgreen?logo=javascript&labelColor=grey&logoColor=white)](https://live.talknicer.com)
[![Google js-genai](https://img.shields.io/badge/Gemini_Live-js--genai_1.10-blue?logo=googlegemini&logoColor=white)](https://github.com/googleapis/js-genai)
[![Katex LaTeX](https://img.shields.io/badge/LaTeX-marked+katex-blue?logo=latex)](https://www.npmjs.com/package/marked-katex-extension)
[![MIT License](https://img.shields.io/badge/License-MIT-green?logo=openaccess&logoColor=white)](https://opensource.org/licenses/MIT)
[![Donate](https://img.shields.io/badge/Donate-gold?logo=paypal)](https://paypal.me/jsalsman)

This **Gemini Live Voice to Text Realtime Stream** running at [live.talknicer.com](https://live.talknicer.com) is a web application that provides a free, live, real-time voice-to-text large language model interaction experience using Google's new `js-genai` API. This project harnesses the power of Gemini 3.8 Live in real-time to provide a seamless voice-driven experience for users, allowing them to chat with the model while reading the output instead of having to wait much longer for synthesized speech, which can't be skimmed. Google Search is available, along with image upload (including from the camera on mobile), text input (including pasting), and both markdown and LaTeX output display. It runs entirely in the browser after the API key cookie is set, and was built in Firebase Studio with about 90% vibe coding and deployed on Google Cloud Run.

<img src="screenshot.png" width="300" alt="Screenshot">

## Key features:
*   **Real-time Voice Input:** Sends speech directly to the model as you speak, providing immediate and blazingly fast responses.
*   **Interactive Conversation:** Allows users to engage in a continuous conversation with the model. Output is rendered correctly from both markdown and LaTeX. Text input, including from copy/paste, is available when needed.
*   **Google Search Integration**: The `gemini-3.8-live` model performs Google searches on request for up to date information.
*   **Image upload:** Including from the camera on mobile devices.
*   **Context preservation:** The discussion output, along with uploaded images and transcribed turns, is preserved across Stop/Start Listening.
*   **Single-Page Application:** The entire client-side logic resides within a single HTML file (`gemini-live.html`), simplifying deployment and enhancing user experience.
*   **Client-Side JavaScript:** The core functionality, including voice capture, transcription, and interaction with the js-genai API, is implemented in JavaScript, making the application highly responsive.
*   **Secure API Key Management:** Utilizes Flask to securely manage the API key by setting it as a cookie. The user is asked to provide their own key, preventing the need to hardcode an API key or run in to rate limits.
*   **Connection Errors:** Connection failures are shown in the app and restore the listening controls without deleting your key.

## Technology stack:
*   **JavaScript:** For client-side logic, voice recording, and LLM API interaction.
*   **Flask:** A lightweight web framework for setting the API key cookie and serving the HTML, entirely in `main.py`.
*   **HTML/CSS/JS:** The single `gemini-live.html` file contains the entire client application.

## Execution:
To run the server: `python -m flask --app main run` and then visit the endpoint from a browser where the API key cookie can be set.

Gemini 3.8 Live requires `AUDIO` responses. The app enables input and output audio transcription, streams the response transcript to the page, and discards returned audio so responses remain silent. Google Search is enabled; built-in Python code execution and generated images are not supported by this model. See the [model documentation](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-live) and [Live API transcription guide](https://ai.google.dev/gemini-api/docs/live-guide#audio-transcriptions).

## Requirements:
The server only needs `Flask` installed (`pip install flask`), but the client JavaScript uses Google's `js-genai`, and the `marked`, `katex`, and `marked-katex-extension` libraries, none of which need to be installed.

If you just serve the `gemini-live.html` file from `localhost` with a hardcoded API key, you don't need Flask.

## Browser regression tests:

Install the server dependencies in `.venv` and install `playwright` in the Python environment used to run the tests. The tests use system Chromium when available, or the browser installed by `python -m playwright install chromium`. You can override the browser executable with `CHROMIUM_PATH`.

Run `python -m unittest discover -s tests -v` from the repository root. The suite starts its own Flask server, loads the real GenAI SDK, captures its Live WebSocket messages, and supplies simulated Gemini responses. It checks native-audio setup, microphone PCM delivery, streaming transcription, context restoration, and failure cleanup without a real API key or billable Gemini requests. Public CDN assets are fetched with system certificate verification for compatibility with cloud proxies.

## Documentation:
* https://ai.google.dev/gemini-api/docs/live
* https://googleapis.github.io/js-genai/release_docs/index.html
* https://github.com/googleapis/js-genai

## License:
This code is released under the free MIT License.

By Jim Salsman, April 11-July 21, 2025.
