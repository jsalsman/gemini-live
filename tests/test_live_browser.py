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
PLOT = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII='


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
        self.flash_requests = []
        self.flash_responses = []
        self.pending_flash = []
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
        # Release held mock routes after cancellation so Playwright can shut down cleanly.
        for route in self.pending_flash:
            route.abort('aborted')
        self.context.close()
        self.assertEqual(self.errors, [])

    def fetch_verified_asset(self, route):
        # Block accidental authentication traffic: only public library CDNs are allowed.
        host = urllib.parse.urlparse(route.request.url).hostname
        if host == 'generativelanguage.googleapis.com':
            self.flash_requests.append({
                'url': route.request.url,
                'headers': route.request.headers,
                'body': route.request.post_data_json,
            })
            if not self.flash_responses:
                self.errors.append('Unexpected Flash request (all execution must be mocked)')
                route.abort()
                return
            response = self.flash_responses.pop(0)
            if response is None:
                self.pending_flash.append(route)
            else:
                status, body = response
                route.fulfill(status=status, content_type='application/json', body=json.dumps(body))
            return
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

    def tool_call(self, *calls):
        self.sockets[-1].send(json.dumps({'toolCall': {'functionCalls': [
            {'id': call_id, 'name': 'execute_python_task', 'args': {'task': task}}
            for call_id, task in calls
        ]}}))

    def flash_output(self):
        return {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'text': 'First Flash text <img src=x onerror="window.injected=1">'},
            {'executableCode': {'language': 'PYTHON', 'code': 'print(2+2)\n# <script>window.injected=1</script>'}},
            {'codeExecutionResult': {'outcome': 'OUTCOME_OK', 'output': '4\n<script>window.injected=1</script>'}},
            {'inlineData': {'mimeType': 'image/png', 'data': PLOT}},
            {'text': 'Second Flash text'},
            {'inlineData': {'mimeType': 'image/png', 'data': PLOT}},
        ]}}]}

    def type_text(self, text):
        self.page.locator('#sendText').click()
        self.page.locator('#textInput').fill(text)
        self.page.locator('#floatingTextInput button').click()
        self.wait_text('Sent: ' + text)

    def replay(self):
        self.page.locator('#toggleStream').click()
        self.page.wait_for_function("document.getElementById('toggleStream').textContent.includes('Start')")
        offset = len(self.messages)
        self.page.locator('#toggleStream').click()
        return self.wait_message('clientContent', offset)['turns']

    def test_native_audio_setup_and_microphone_pcm(self):
        self.start()
        setup = self.wait_message('setup')
        self.assertEqual(setup['model'], 'models/gemini-3.8-live')
        self.assertEqual(setup['generationConfig']['responseModalities'], ['AUDIO'])
        self.assertIn('inputAudioTranscription', setup)
        self.assertIn('outputAudioTranscription', setup)
        self.assertIn('helpful assistant', setup['systemInstruction']['parts'][0]['text'])
        self.assertEqual(setup['tools'][0], {'googleSearch': {}})
        function = setup['tools'][1]['functionDeclarations'][0]
        self.assertEqual(function['name'], 'execute_python_task')
        self.assertEqual(function['behavior'], 'BLOCKING')
        self.assertNotIn('thinkingConfig', setup['generationConfig'])
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
        self.assertIn("You're saying: Hello there", self.page.locator('#output').inner_text())
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
        self.send({'turnComplete': True})
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

    def test_delegation_multiple_parts_plots_matched_function_responses_and_replay(self):
        self.start()
        self.flash_responses = [(200, self.flash_output()), (200, self.flash_output())]
        self.tool_call(('plot-a', 'Calculate and plot 2+2'), ('plot-b', 'Plot sin(x)'))
        self.page.wait_for_function("document.querySelectorAll('.delegation img').length === 4")
        self.page.wait_for_function("[...document.querySelectorAll('.delegation img')].every(img => img.complete && img.naturalWidth > 0)")
        deadline = time.monotonic() + 10
        while len([m for m in self.messages if 'toolResponse' in m]) < 2 and time.monotonic() < deadline:
            self.page.wait_for_timeout(25)
        responses = [m['toolResponse']['functionResponses'][0] for m in self.messages if 'toolResponse' in m]
        self.assertEqual({r['id'] for r in responses}, {'plot-a', 'plot-b'})
        self.assertTrue(all(r['name'] == 'execute_python_task' and r['response']['ok'] for r in responses))
        self.assertTrue(all(r['response']['plots'] == 2 for r in responses))
        self.assertEqual(len(self.flash_requests), 2)
        for request in self.flash_requests:
            self.assertTrue(request['url'].endswith('gemini-3.8-flash:generateContent'))
            self.assertNotIn('placeholder', request['url'])
            self.assertEqual(request['headers']['x-goog-api-key'], 'browser-regression-placeholder')
            self.assertEqual(request['body']['tools'], [{'codeExecution': {}}])
            self.assertEqual(request['body']['generationConfig']['thinkingConfig'], {'thinkingLevel': 'low'})
        self.assertEqual(self.page.locator('.delegation pre').count(), 8)
        self.assertIn('Second Flash text', self.page.locator('.delegation').first.inner_text())
        self.assertIsNone(self.page.evaluate('window.injected'))
        self.assertEqual(self.page.locator('.delegation script').count(), 0)
        self.send({'outputTranscription': {'text': 'The result is four.'}, 'turnComplete': True})
        self.wait_text('The result is four.')
        turns = self.replay()
        self.assertEqual(sum('plot(s) displayed.' in p.get('text', '') for t in turns for p in t['parts']), 2)
        self.assertEqual(self.page.locator('.delegation img').count(), 4)

    def test_flash_configuration_failure_keeps_key_live_session_and_matching_id(self):
        self.start()
        self.flash_responses = [(400, {'error': {'message': 'invalid configuration browser-regression-placeholder'}})]
        self.tool_call(('bad-config', 'Calculate 2+2'))
        response = self.wait_message('toolResponse')['functionResponses'][0]
        self.assertEqual(response['id'], 'bad-config')
        self.assertFalse(response['response']['ok'])
        self.assertIn('low thinking / codeExecution', response['response']['error'])
        self.assertIn('Your key was kept', self.page.locator('.delegation').inner_text())
        self.assertNotIn('placeholder', self.page.locator('#output').inner_text())
        self.assertIn('gemini_api_key=browser-regression-placeholder', self.page.evaluate('document.cookie'))
        self.assertIn('Stop', self.page.locator('#toggleStream').inner_text())
        self.assertEqual(len(self.flash_requests), 1)

    def test_cancellation_and_stop_do_not_send_stale_function_responses(self):
        self.start()
        self.flash_responses = [None]
        self.tool_call(('cancelled', 'Plot x*x'))
        self.wait_text('Running managed Python')
        self.page.wait_for_timeout(100)
        self.sockets[-1].send(json.dumps({'toolCallCancellation': {'ids': ['cancelled']}}))
        self.wait_text('Execution cancelled')
        self.assertFalse(any('toolResponse' in m for m in self.messages))
        self.flash_responses = [None]
        self.tool_call(('stopped', 'Plot sin(x)'))
        self.page.wait_for_timeout(100)
        turns = self.replay()
        self.assertFalse(any('toolResponse' in m for m in self.messages))
        self.assertFalse(any('task pending' in p.get('text', '') for t in turns for p in t['parts']))

    def test_typed_interrupt_keeps_late_model_chunks_before_interrupting_user(self):
        self.start()
        self.type_text('Original request')
        self.send({'outputTranscription': {'text': 'Partial answer'}})
        self.wait_text('Partial answer')
        self.type_text('Interrupting request')
        # A final chunk can arrive after sendClientContent, before interrupted.
        self.send({'outputTranscription': {'text': ' final chunk'}})
        self.send({'interrupted': True})
        self.send({'turnComplete': True})
        self.send({'outputTranscription': {'text': 'New answer'}, 'turnComplete': True})
        self.wait_text('New answer')
        turns = self.replay()
        self.assertEqual([(t['role'], t['parts'][0].get('text')) for t in turns[:4]], [
            ('user', 'Original request'), ('model', 'Partial answer final chunk'),
            ('user', 'Interrupting request'), ('model', 'New answer'),
        ])

    def test_image_interrupt_preserves_replay_order(self):
        import base64
        self.start()
        self.type_text('Original request')
        self.send({'outputTranscription': {'text': 'Partial answer'}})
        self.wait_text('Partial answer')
        with self.page.expect_file_chooser() as chooser:
            self.page.locator('#sendImage').click()
        chooser.value.set_files({'name': 'plot.png', 'mimeType': 'image/png', 'buffer': base64.b64decode(PLOT)})
        self.wait_text('Image sent successfully')
        self.send({'interrupted': True})
        turns = self.replay()
        self.assertEqual(turns[0]['parts'][0]['text'], 'Original request')
        self.assertEqual(turns[1], {'role': 'model', 'parts': [{'text': 'Partial answer'}]})
        self.assertEqual(turns[2]['role'], 'user')
        self.assertEqual(turns[2]['parts'][0]['inlineData']['data'], PLOT)

    def test_voice_barge_in_before_interrupt_event_preserves_model_before_user(self):
        self.start()
        self.send({'inputTranscription': {'text': 'Original speech', 'finished': True}})
        self.send({'outputTranscription': {'text': 'Partial spoken response'}})
        self.wait_text('Partial spoken response')
        self.send({'inputTranscription': {'text': 'Interrupting speech'}})
        self.wait_text('Interrupting speech')
        self.send({'interrupted': True})
        self.send({'turnComplete': True})
        self.send({'outputTranscription': {'text': 'New response'}, 'turnComplete': True})
        self.wait_text('New response')
        turns = self.replay()
        self.assertEqual([(t['role'], t['parts'][0].get('text')) for t in turns[:4]], [
            ('user', 'Original speech'), ('model', 'Partial spoken response'),
            ('user', 'Interrupting speech'), ('model', 'New response'),
        ])

    def test_late_input_chunks_remain_one_utterance_before_response(self):
        self.start()
        self.send({'inputTranscription': {'text': 'Hello '}})
        self.send({'outputTranscription': {'text': 'Welcome'}})
        self.wait_text('Welcome')
        self.send({'inputTranscription': {'text': 'there'}})
        self.send({'outputTranscription': {'text': '!'}, 'turnComplete': True})
        self.wait_text('You said: Hello there')
        turns = self.replay()
        self.assertEqual(turns[:2], [
            {'role': 'user', 'parts': [{'text': 'Hello there'}]},
            {'role': 'model', 'parts': [{'text': 'Welcome!'}]},
        ])
        self.assertEqual(self.page.locator('#output .info').filter(has_text='You said:').count(), 1)

    def test_model_markdown_cannot_execute_script_from_tool_or_user_content(self):
        self.start()
        self.send({'modelTurn': {'parts': [{'text': '<img src=x onerror="window.injected=1"> **safe** <script>window.injected=2</script>'}]}, 'turnComplete': True})
        self.wait_text('safe')
        self.assertIsNone(self.page.evaluate('window.injected'))
        self.assertEqual(self.page.locator('#output script, #output [onerror]').count(), 0)

    def test_audio_before_transcript_reserves_interrupted_model_position(self):
        self.start()
        self.type_text('Original request')
        self.send({'modelTurn': {'parts': [{'inlineData': {'mimeType': 'audio/pcm;rate=24000', 'data': 'AAA='}}]}})
        self.page.wait_for_timeout(50)
        self.type_text('Interrupting request')
        self.send({'outputTranscription': {'text': 'Delayed partial transcript'}})
        self.send({'interrupted': True})
        self.wait_text('Delayed partial transcript')
        turns = self.replay()
        self.assertEqual([(t['role'], t['parts'][0].get('text')) for t in turns[:3]], [
            ('user', 'Original request'), ('model', 'Delayed partial transcript'), ('user', 'Interrupting request'),
        ])

    def test_invalid_task_type_returns_failure_without_request_or_page_error(self):
        self.start()
        self.sockets[-1].send(json.dumps({'toolCall': {'functionCalls': [
            {'id': 'invalid-type', 'name': 'execute_python_task', 'args': {'task': 123}},
        ]}}))
        response = self.wait_message('toolResponse')['functionResponses'][0]
        self.assertEqual(response['id'], 'invalid-type')
        self.assertEqual(response['response']['code'], 'INVALID_TASK')
        self.assertEqual(self.flash_requests, [])

    def test_interruption_message_tail_updates_original_model_record(self):
        self.start()
        self.type_text('Original request')
        self.send({'outputTranscription': {'text': 'Partial answer'}})
        self.wait_text('Partial answer')
        self.type_text('Interrupting request')
        self.send({'interrupted': True, 'outputTranscription': {'text': ' final tail'}, 'turnComplete': True})
        self.wait_text('final tail')
        turns = self.replay()
        self.assertEqual(turns[:3], [
            {'role': 'user', 'parts': [{'text': 'Original request'}]},
            {'role': 'model', 'parts': [{'text': 'Partial answer final tail'}]},
            {'role': 'user', 'parts': [{'text': 'Interrupting request'}]},
        ])

    def test_tail_after_interrupted_stays_in_original_record_until_turn_complete(self):
        self.start()
        self.type_text('Original request')
        self.send({'outputTranscription': {'text': 'Partial answer'}})
        self.wait_text('Partial answer')
        self.type_text('Interrupting request')
        self.send({'interrupted': True})
        self.send({'outputTranscription': {'text': ' late'}})
        self.send({'outputTranscription': {'text': ' tail'}, 'turnComplete': True})
        self.send({'outputTranscription': {'text': 'New answer'}, 'turnComplete': True})
        self.wait_text('New answer')
        turns = self.replay()
        self.assertEqual(turns[:4], [
            {'role': 'user', 'parts': [{'text': 'Original request'}]},
            {'role': 'model', 'parts': [{'text': 'Partial answer late tail'}]},
            {'role': 'user', 'parts': [{'text': 'Interrupting request'}]},
            {'role': 'model', 'parts': [{'text': 'New answer'}]},
        ])
        self.assertEqual(self.page.locator('#output .text').count(), 2)

    def test_tool_boundary_keeps_preamble_execution_and_continuation_in_order(self):
        self.start()
        self.type_text('Calculate 2+2')
        self.flash_responses = [(200, self.flash_output())]
        # Exercise content and toolCall in one envelope: consume the preamble first.
        self.sockets[-1].send(json.dumps({
            'serverContent': {'outputTranscription': {'text': 'I will calculate that.'}},
            'toolCall': {'functionCalls': [
                {'id': 'segmented', 'name': 'execute_python_task', 'args': {'task': 'Calculate 2+2'}},
            ]},
        }))
        self.wait_message('toolResponse')
        self.send({'outputTranscription': {'text': 'The answer is four.'}, 'turnComplete': True})
        self.wait_text('The answer is four.')
        turns = self.replay()
        self.assertEqual(turns[0], {'role': 'user', 'parts': [{'text': 'Calculate 2+2'}]})
        self.assertEqual(turns[1], {'role': 'model', 'parts': [{'text': 'I will calculate that.'}]})
        self.assertTrue(turns[2]['parts'][0]['text'].startswith('Managed Python task: Calculate 2+2'))
        self.assertEqual(turns[3], {'role': 'model', 'parts': [{'text': 'The answer is four.'}]})
        self.assertEqual(self.page.locator('#output .text').count(), 2)

    def test_separate_tool_call_and_duplicate_id_do_not_reorder_model_segments(self):
        self.start()
        self.type_text('Calculate 2+2')
        self.send({'outputTranscription': {'text': 'Preamble.'}})
        self.wait_text('Preamble.')
        self.flash_responses = [(200, self.flash_output())]
        self.tool_call(('separate', 'Calculate 2+2'))
        self.wait_message('toolResponse')
        self.send({'outputTranscription': {'text': 'Continuation '}})
        self.wait_text('Continuation ')
        self.tool_call(('separate', 'Calculate 2+2'))
        self.send({'outputTranscription': {'text': 'tail.'}, 'turnComplete': True})
        self.wait_text('Continuation tail.')
        turns = self.replay()
        self.assertEqual(turns[1], {'role': 'model', 'parts': [{'text': 'Preamble.'}]})
        self.assertTrue(turns[2]['parts'][0]['text'].startswith('Managed Python task: Calculate 2+2'))
        self.assertEqual(turns[3], {'role': 'model', 'parts': [{'text': 'Continuation tail.'}]})
        self.assertEqual(self.page.locator('.delegation').count(), 1)
        self.assertEqual(self.page.locator('#output .text').count(), 2)
        self.assertEqual(len(self.flash_requests), 1)

    def test_voice_transcription_spanning_interruption_remains_one_user_turn(self):
        self.start()
        self.send({'inputTranscription': {'text': 'Original speech', 'finished': True}})
        self.send({'outputTranscription': {'text': 'Partial answer'}})
        self.wait_text('Partial answer')
        self.send({'inputTranscription': {'text': 'Interrupting '}})
        self.wait_text('Interrupting ')
        self.send({'interrupted': True, 'outputTranscription': {'text': ' tail'}})
        self.send({'inputTranscription': {'text': 'speech', 'finished': True}})
        self.send({'turnComplete': True})
        self.send({'outputTranscription': {'text': 'New answer'}, 'turnComplete': True})
        self.wait_text('New answer')
        turns = self.replay()
        self.assertEqual(turns[:4], [
            {'role': 'user', 'parts': [{'text': 'Original speech'}]},
            {'role': 'model', 'parts': [{'text': 'Partial answer tail'}]},
            {'role': 'user', 'parts': [{'text': 'Interrupting speech'}]},
            {'role': 'model', 'parts': [{'text': 'New answer'}]},
        ])
        self.assertEqual(self.page.locator('#output .info').filter(has_text='You said:').count(), 2)

    def test_interruption_does_not_reset_execution_budget_for_same_utterance(self):
        self.start()
        self.send({'inputTranscription': {'text': 'Calculate '}})
        self.wait_text('Calculate ')
        # Supply a fourth mock so a mistaken budget reset yields success, not a
        # network failure; the fourth response is reserved for a later user turn.
        self.flash_responses = [(200, self.flash_output()) for _ in range(4)]
        for index in range(3):
            offset = len(self.messages)
            self.tool_call((f'budget-{index}', 'Calculate 2+2'))
            result = self.wait_message('toolResponse', offset)['functionResponses'][0]
            self.assertTrue(result['response']['ok'])
        self.send({'interrupted': True})
        self.send({'inputTranscription': {'text': 'again', 'finished': True}})
        self.wait_text('again')
        offset = len(self.messages)
        self.tool_call(('same-utterance', 'Calculate 2+2'))
        result = self.wait_message('toolResponse', offset)['functionResponses'][0]
        self.assertEqual(result['id'], 'same-utterance')
        self.assertEqual(result['response']['code'], 'CALL_LIMIT')
        self.assertEqual(len(self.flash_requests), 3)
        self.wait_text('You said: Calculate again')
        self.send({'turnComplete': True})
        self.type_text('New calculation request')
        offset = len(self.messages)
        self.tool_call(('new-utterance', 'Calculate 2+2'))
        result = self.wait_message('toolResponse', offset)['functionResponses'][0]
        self.assertTrue(result['response']['ok'])
        self.assertEqual(len(self.flash_requests), 4)


if __name__ == '__main__':
    unittest.main()
