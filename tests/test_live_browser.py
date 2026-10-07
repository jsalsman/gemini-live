"""Browser regressions using the real GenAI SDK and a simulated Live WebSocket.

Requires Playwright and Chromium. HTTPS assets use Python's verified system CA
bundle for compatibility with cloud proxy certificates; TLS is never disabled.
No Gemini credentials or billable API requests are used.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class LiveBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        cls.base = f'http://127.0.0.1:{port}'
        python = ROOT / '.venv/bin/python'
        cls.server = subprocess.Popen(
            [str(python), '-m', 'flask', '--app', 'main', 'run',
             '--host', '127.0.0.1', '--port', str(port)], cwd=ROOT,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(100):
                try:
                    with urllib.request.urlopen(cls.base, timeout=1) as response:
                        assert response.status == 200
                    break
                except OSError:
                    if cls.server.poll() is not None:
                        raise RuntimeError('Flask failed to start')
                    time.sleep(0.05)
            else:
                raise RuntimeError('Flask readiness timed out')
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch(
                executable_path=os.environ.get('CHROMIUM_PATH') or shutil.which('chromium'),
                args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'],
            )
            cls.assets = {}
        except Exception:
            cls.server.terminate()
            cls.server.wait(timeout=5)
            if hasattr(cls, 'playwright'):
                cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context()
        self.context.grant_permissions(['microphone'], origin=self.base)
        self.context.route('https://**/*', self.fetch_verified_asset)
        self.messages = []
        self.sockets = []
        self.errors = []
        self.context.route_web_socket('wss://**/*', self.handle_socket)
        self.page = self.context.new_page()
        self.page.on('pageerror', lambda error: self.errors.append(str(error)))
        self.page.goto(self.base)
        self.page.locator('input[name="api_key"]').fill('browser-regression-placeholder')
        self.page.get_by_role('button', name='Run', exact=True).click()
        self.page.wait_for_function(
            "document.getElementById('output')?.textContent.includes(\"Click 'Start Listening'\")"
        )

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [])

    def fetch_verified_asset(self, route):
        # Block accidental authentication traffic: only public library CDNs are allowed.
        host = urllib.parse.urlparse(route.request.url).hostname
        if host not in {'esm.sh', 'cdn.jsdelivr.net'}:
            self.errors.append(f'Unexpected external HTTP request to {host}')
            route.abort()
            return
        if route.request.url not in self.assets:
            with urllib.request.urlopen(route.request.url, timeout=30) as response:
                headers = {key: value for key, value in response.headers.items()
                           if key.lower() not in {'content-length', 'content-encoding',
                                                  'transfer-encoding', 'connection'}}
                self.assets[route.request.url] = (response.status, headers, response.read())
        status, headers, body = self.assets[route.request.url]
        route.fulfill(status=status, headers=headers, body=body)

    def handle_socket(self, ws):
        self.sockets.append(ws)
        def receive(data):
            message = json.loads(data)
            self.messages.append(message)
            if 'setup' in message:
                ws.send(json.dumps({'setupComplete': {}}))
        ws.on_message(receive)

    def start(self):
        self.page.locator('#toggleStream').click()
        self.page.wait_for_function(
            "document.getElementById('output')?.textContent.includes('Connected to Gemini. Listening...')"
        )
        self.assertGreater(len(self.sockets), 0)

    def send(self, content):
        self.sockets[-1].send(json.dumps({'serverContent': content}))

    def wait_text(self, text):
        self.page.wait_for_function(
            "text => document.getElementById('output')?.textContent.includes(text)", arg=text
        )

    def wait_message(self, key, start=0):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for message in self.messages[start:]:
                if key in message:
                    return message[key]
            self.page.wait_for_timeout(25)
        self.fail(f'No {key} message received')

    def test_native_audio_setup_and_microphone_pcm(self):
        self.start()
        setup = self.wait_message('setup')
        self.assertEqual(setup['model'], 'models/gemini-3.8-live')
        self.assertEqual(setup['generationConfig']['responseModalities'], ['AUDIO'])
        self.assertIn('inputAudioTranscription', setup)
        self.assertIn('outputAudioTranscription', setup)
        self.assertIn('helpful assistant', setup['systemInstruction']['parts'][0]['text'])
        self.assertEqual(setup['tools'], [{'googleSearch': {}}])
        self.assertNotIn('seed', setup['generationConfig'])
        audio = self.wait_message('realtimeInput')['audio']
        self.assertEqual(audio['mimeType'], 'audio/pcm;rate=16000')
        self.assertTrue(audio['data'])
        self.assertTrue(self.page.locator('#sendText').is_visible())

    def test_streaming_transcript_final_chunk_and_context_restart(self):
        self.start()
        self.send({'inputTranscription': {'text': 'Hello there'},
                   'outputTranscription': {'text': 'Hello **'},
                   'modelTurn': {'parts': [{'inlineData': {
                       'mimeType': 'audio/pcm;rate=24000', 'data': 'AAA='}}]}})
        self.wait_text('Hello **')
        self.assertIn('You said: Hello there', self.page.locator('#output').inner_text())
        self.send({'generationComplete': True, 'outputTranscription': {'text': 'world'}})
        self.wait_text('Hello **world')
        self.send({'turnComplete': True, 'outputTranscription': {'text': '**!'}})
        self.page.wait_for_function("document.querySelector('#output .text strong')?.textContent === 'world'")
        self.assertEqual(self.page.locator('#output .text').count(), 1)
        self.page.locator('#toggleStream').click()
        self.page.wait_for_function("document.getElementById('toggleStream').textContent.includes('Start')")
        offset = len(self.messages)
        self.page.locator('#toggleStream').click()
        content = self.wait_message('clientContent', start=offset)
        turns = content['turns']
        self.assertIn({'role': 'user', 'parts': [{'text': 'Hello there'}]}, turns)
        self.assertEqual(sum(turn == {'role': 'model', 'parts': [{'text': 'Hello **world**!'}]}
                             for turn in turns), 1)

    def test_multiple_text_parts_thoughts_and_interruption(self):
        self.start()
        self.send({'modelTurn': {'parts': [
            {'text': 'hidden reasoning', 'thought': True},
            {'text': 'First '}, {'text': 'answer'},
        ]}})
        self.wait_text('First answer')
        self.assertNotIn('hidden reasoning', self.page.locator('#output').inner_text())
        self.send({'interrupted': True})
        self.send({'outputTranscription': {'text': 'Next answer'}, 'turnComplete': True})
        self.wait_text('Next answer')
        self.assertEqual(self.page.locator('#output .text').count(), 2)
        self.assertEqual(self.page.locator('#output .text').nth(1).inner_text().strip(), 'Next answer')

    def test_socket_failure_preserves_key_and_resets_controls(self):
        self.start()
        self.sockets[-1].close(code=1007, reason='Requested model is unavailable')
        self.wait_text('Requested model is unavailable')
        self.page.wait_for_function("document.getElementById('toggleStream').textContent.includes('Start')")
        self.assertFalse(self.page.locator('#sendText').is_visible())
        self.assertIn('gemini_api_key=browser-regression-placeholder', self.page.evaluate('document.cookie'))

    def test_microphone_denied_preserves_key_and_resets_controls(self):
        self.page.evaluate("""() => {
            navigator.mediaDevices.getUserMedia = async () => {
                throw new DOMException('Microphone permission denied', 'NotAllowedError');
            };
        }""")
        self.page.locator('#toggleStream').click()
        self.wait_text('Microphone permission denied')
        self.assertIn('Start', self.page.locator('#toggleStream').inner_text())
        self.assertEqual(self.sockets, [])
        self.assertIn('gemini_api_key=browser-regression-placeholder', self.page.evaluate('document.cookie'))


if __name__ == '__main__':
    unittest.main()
