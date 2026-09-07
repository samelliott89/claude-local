"""Check the vLLM thinking adapter and launch preset without loading a GPU."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('shim', root / 'shim.py')
shim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(shim)
body = json.dumps({'chat_template_kwargs': {'other': 1}, 'messages': [], 'tools': []}).encode()
for mode in ('off', 'on'):
    shim.THINKING = mode
    with patch.dict(os.environ, {'CLAUDE_LOCAL_THINK_PROVIDER': 'vllm'}):
        result = json.loads(shim.set_thinking(body))
    assert result['chat_template_kwargs'] == {'other': 1, 'enable_thinking': mode == 'on'}
    assert result['tools'] == []
with patch.dict(os.environ, {'CLAUDE_LOCAL_THINK_PROVIDER': 'sglang'}):
    assert json.loads(shim.set_thinking(body))['chat_template_kwargs'] == {'other': 1}
print('PASS vLLM thinking on/off and SGLang isolation')

with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    bin_dir = base / 'bin'
    bin_dir.mkdir()
    def script(name, text):
        p = bin_dir / name
        p.write_text('#!/bin/sh\n' + text)
        p.chmod(0o755)
    script('curl', 'test -f "$TEST_CAPTURE"\n')
    script('ollama', 'exit 0\n')
    script('vllm', 'printf "%s\\n" "$@" >"$TEST_CAPTURE"\n')
    env = dict(os.environ, PATH=f'{bin_dir}:' + os.environ['PATH'],
               XDG_RUNTIME_DIR=tmp, VLLM_VENV=tmp, TEST_CAPTURE=str(base / 'args'),
               VLLM_PORT='18999')
    subprocess.run(['sh', str(root / 'vllm-serve'), 'start'], env=env, check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15)
    args = (base / 'args').read_text().splitlines()
    assert args[0] == 'serve'
    assert args[args.index('--max-model-len') + 1] == '131072'
    assert args[args.index('--host') + 1] == '127.0.0.1'
    draft = json.loads(args[args.index('--speculative-config') + 1])
    assert draft['method'] == 'dflash' and draft['num_speculative_tokens'] == 7
print('PASS autostart preset arguments (mock server; no GPU load)')

import io
import urllib.request
request_body = {'model': 'local', 'messages': [{'role': 'user', 'content': 'keep all of this'}], 'max_tokens': 8192, 'stream': True}
request = urllib.request.Request('http://localhost:8000/v1/messages', data=json.dumps(request_body).encode(), headers={'Content-Type': 'application/json'})
error = b'This model\'s maximum context length is 32768 tokens. Your prompt contains at least 24577 input tokens.'
with patch.dict(os.environ, {'CLAUDE_LOCAL_THINK_PROVIDER': 'vllm'}):
    with patch.object(shim.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"input_tokens":28000}')):
        retry = shim.context_retry(request, error)
    result = json.loads(retry.data)
    assert result['max_tokens'] == 4704
    assert result['messages'] == request_body['messages'] and result['stream'] is True
    assert shim.context_retry(request, b'unrelated invalid tool schema') is None
    with patch.object(shim.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"input_tokens":32768}')):
        assert shim.context_retry(request, error) is None
with patch.dict(os.environ, {'CLAUDE_LOCAL_THINK_PROVIDER': 'sglang'}):
    assert shim.context_retry(request, error) is None
print('PASS context retry: exact count, prompt preserved, unrelated errors and full inputs unchanged')

# Claude Code sends the beta query suffix, including during compaction.
request = urllib.request.Request('http://localhost:8000/v1/messages?beta=true', data=json.dumps(request_body).encode())
with patch.dict(os.environ, {'CLAUDE_LOCAL_THINK_PROVIDER': 'vllm'}):
    with patch.object(shim.urllib.request, 'urlopen', return_value=io.BytesIO(b'{"input_tokens":28000}')) as opened:
        retry = shim.context_retry(request, error)
        assert opened.call_args.args[0].full_url == 'http://localhost:8000/v1/messages/count_tokens?beta=true'
        assert retry.full_url == request.full_url
        assert json.loads(retry.data)['max_tokens'] == 4704
print('PASS beta query suffix on token counting and generation retry')
