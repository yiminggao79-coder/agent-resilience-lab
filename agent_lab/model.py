"""Isolated, bounded HTTP transport for explicit provider protocol capabilities."""
import hashlib
import http.client
import codecs
from contextlib import contextmanager
from contextvars import ContextVar
import threading
import importlib.util
import json
import os
import math
from pathlib import Path
import subprocess
import selectors
import signal
import ssl
import socket
import sys
import time
from urllib.parse import urlsplit
import urllib.error
import urllib.request

# The worker runs with -I -S. Load only the trusted sibling rather than adding cwd
# to sys.path or permitting a workspace module to shadow a standard library.
_protocol_path = Path(__file__).resolve().with_name('protocols.py')
_spec = importlib.util.spec_from_file_location('_agent_lab_protocols', _protocol_path)
_protocols = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_protocols)
MAX_RESPONSE = 2 * 1024 * 1024
MAX_CANONICAL = 2 * MAX_RESPONSE
MAX_IPC_FRAME = MAX_CANONICAL + 4096
MAX_WORKER_OUTPUT = 8 * MAX_RESPONSE
MAX_WORKER_STDERR = 65536
MAX_STREAM_REQUEST = 8 * MAX_RESPONSE
_PENDING = object()
HTTP_DIAGNOSTIC_LABELS = ('model', 'max_tokens', 'thinking', 'store', 'tools', 'stream', 'balance',
                          'unsupported', 'invalid', 'insufficient', 'messages', 'stream_options', 'tool_choice')
PROTOCOL_DIAGNOSTIC_LABELS = ('output_token_limit', 'incomplete_stream', 'incomplete_response',
    'incomplete_items', 'incomplete_sse_event', 'invalid_sse_json', 'invalid_response',
    'invalid_tool_arguments', 'invalid_native_content', 'invalid_reasoning_content',
    'unsupported_output', 'completed_items_mismatch', 'invalid_stream', 'json_parse')
DIAGNOSTIC_LABELS = frozenset(HTTP_DIAGNOSTIC_LABELS + PROTOCOL_DIAGNOSTIC_LABELS + (
    'phase_tls_setup', 'phase_open', 'phase_headers', 'phase_body', 'phase_ipc',
    'incomplete_chunk', 'incomplete_read', 'peer_closed', 'connection_reset', 'tls', 'dns',
    'network', 'io', 'timeout', 'http_framing', 'http_status', 'protocol', 'tls_certificate', 'tls_eof',
    'worker_framing', 'worker_no_final', 'worker_exit', 'worker_start', 'worker_failure',
    'deadline', 'size_limit', 'cancelled', 'unknown'))


def normalize_diagnostics(value):
    """A bounded vocabulary, never arbitrary exception/provider/worker strings."""
    if not isinstance(value, list):
        return []
    result = []
    for label in value[:len(DIAGNOSTIC_LABELS)]:
        if isinstance(label, str) and label in DIAGNOSTIC_LABELS and label not in result:
            result.append(label)
    return result


class ModelError(Exception):
    def __init__(self, code, retryable=False, uncertain=False, http_status=None, diagnostic=None,
                 response_body_bytes=None):
        self.code, self.retryable, self.uncertain = code, retryable, uncertain
        self.http_status = http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        self.diagnostic = normalize_diagnostics(diagnostic) if diagnostic is not None else None
        # Only bytes actually returned by HTTP read/read1, never Content-Length,
        # chunk framing, inferred partial reads, IPC sizes, or billable tokens.
        self.response_body_bytes = (response_body_bytes if type(response_body_bytes) is int
                                   and 0 <= response_body_bytes <= MAX_RESPONSE + 8192 else None)
        super().__init__(code)


@contextmanager
def _owner_pipe(environment):
    """One parent writer, one trusted worker read FD; no project inheritance."""
    if os.name != 'posix':
        raise ModelError('transport_start_failed', diagnostic=['phase_ipc', 'worker_start'])
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        raise ModelError('transport_start_failed', diagnostic=['phase_ipc', 'worker_start']) from None
    try:
        yield read_fd, {**environment, 'AGENT_LAB_TRANSPORT_OWNER_FD': str(read_fd)}
    finally:
        os.close(read_fd)
        os.close(write_fd)


def _watch_owner():
    """Exit a trusted HTTP worker on owner EOF, even during blocking reads."""
    value = os.environ.pop('AGENT_LAB_TRANSPORT_OWNER_FD', None)
    if value is None:
        return  # Direct legacy worker protocol remains usable by offline probes.
    try:
        descriptor = int(value)
        os.fstat(descriptor)
    except (ValueError, OSError):
        os._exit(70)
    def watch():
        try:
            while os.read(descriptor, 1):
                pass
        except OSError:
            pass
        finally:
            os.close(descriptor)
        os._exit(70)
    threading.Thread(target=watch, daemon=True, name='model-owner-watch').start()


class OpenAIModel:
    def __init__(self, model, base_url=None, api_key_env=None, allow_api=False,
                 protocol='chat', stream=False, token_parameter='max_completion_tokens', parallel_parameter=True,
                 thinking=None, reasoning_effort=None, ca_bundle=None, on_text=None):
        if on_text is not None and not callable(on_text):
            raise ValueError('invalid_text_callback')
        self.on_text, self.last_text_streamed = on_text, False
        self._cancellation_checks = ContextVar('model_cancellation_' + str(id(self)), default=())
        native = protocol == 'anthropic'
        base_env = 'ANTHROPIC_BASE_URL' if native else 'OPENAI_BASE_URL'
        default_base = 'https://api.anthropic.com/v1' if native else 'https://api.openai.com/v1'
        self.base_url = (base_url or os.environ.get(base_env) or default_base).rstrip('/')
        api_key_env = api_key_env if api_key_env is not None else ('ANTHROPIC_API_KEY' if native else 'OPENAI_API_KEY')
        parsed = urlsplit(self.base_url)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost', '::1'))):
            raise ValueError('invalid_api_url')
        if not isinstance(model, str) or not model or len(model) > 200:
            raise ValueError('model_required')
        if not isinstance(api_key_env, str) or not api_key_env or '=' in api_key_env or '\x00' in api_key_env:
            raise ValueError('invalid_api_key_env')
        if api_key_env == 'DEEPSEEK_API_KEY' and self.base_url not in ('https://api.deepseek.com', 'https://api.deepseek.com/anthropic'):
            raise ValueError('unauthorized_api_key_endpoint')
        # Validate explicit capability settings without guessing from model names.
        try:
            _protocols.build_request(protocol, model, [], [], 1, stream, token_parameter, parallel_parameter,
                                     thinking, reasoning_effort)
        except _protocols.ProtocolError as error:
            raise ValueError(str(error))
        if ca_bundle is not None:
            bundle = Path(ca_bundle)
            if not bundle.is_absolute() or not bundle.is_file():
                raise ValueError('invalid_ca_bundle')
            ca_bundle = str(bundle.resolve())
        self.ca_bundle = ca_bundle
        self.model, self.api_key_env, self.allow_api = model, api_key_env, allow_api
        self.protocol, self.stream = protocol, stream
        self.token_parameter, self.parallel_parameter = token_parameter, parallel_parameter
        self.thinking, self.reasoning_effort = thinking, reasoning_effort
        config = {'base_url': self.base_url, 'model': model, 'api_key_env': api_key_env,
                  'protocol': protocol, 'stream': stream, 'token_parameter': token_parameter,
                  'parallel_parameter': parallel_parameter}
        # Preserve f1ddc89 checkpoint identities when new options are omitted.
        config.update({key: value for key, value in {'ca_bundle': ca_bundle, 'thinking': thinking,
                      'reasoning_effort': reasoning_effort}.items() if value is not None})
        self.identity = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

    @contextmanager
    def cancellation_scope(self, check):
        """Host-only scoped checks; nested scopes retain outer cancellation."""
        if not callable(check):
            raise ValueError('invalid_cancel_check')
        token = self._cancellation_checks.set(self._cancellation_checks.get() + (check,))
        try:
            yield
        finally:
            self._cancellation_checks.reset(token)

    def _check_cancelled(self):
        for check in self._cancellation_checks.get():
            try:
                cancelled = check()
            except Exception:
                raise ModelError('model_cancel_check_failed', uncertain=True,
                                 diagnostic=['phase_ipc', 'worker_failure']) from None
            if cancelled:
                raise ModelError('model_request_cancelled', uncertain=True,
                                 diagnostic=['phase_ipc', 'cancelled'])

    def complete(self, messages, tools, timeout, max_tokens):
        self.last_text_streamed = False
        if self.api_key_env == 'DEEPSEEK_API_KEY' and self.base_url not in ('https://api.deepseek.com', 'https://api.deepseek.com/anthropic'):
            raise ModelError('unauthorized_api_key_endpoint')
        use_ipc = self.stream or bool(self._cancellation_checks.get())
        try:
            path, body = _protocols.build_request(self.protocol, self.model, messages, tools, max_tokens,
                                                  self.stream, self.token_parameter, self.parallel_parameter,
                                                  self.thinking, self.reasoning_effort)
            payload = {'url': self.base_url + path, 'body': body, 'timeout': timeout,
                       'protocol': self.protocol, 'stream': self.stream, 'ca_bundle': self.ca_bundle}
            if use_ipc and not self.stream:
                payload['ipc_stream'] = True
            serialized = json.dumps(payload, allow_nan=False)
        except (ValueError, TypeError, KeyError):
            raise ModelError('invalid_model_request')
        if not self.allow_api:
            raise ModelError('api_not_enabled')
        key = os.environ.get(self.api_key_env)
        if not key:
            raise ModelError('api_key_missing')
        environment = {'PATH': os.defpath, 'LANG': 'C.UTF-8', 'AGENT_LAB_TRANSPORT_KEY': key}
        result = None
        if use_ipc:
            result = self._stream_worker(serialized, environment, timeout)
        else:
            try:
                with _owner_pipe(environment) as (owner_fd, owned_environment):
                    proc = subprocess.run([sys.executable, '-I', '-S', str(Path(__file__).resolve())],
                                          input=serialized, text=True, capture_output=True,
                                          env=owned_environment, timeout=timeout, close_fds=True, pass_fds=(owner_fd,))
            except subprocess.TimeoutExpired:
                raise ModelError('model_timeout', uncertain=True, diagnostic=['phase_ipc', 'deadline'])
            except OSError:
                raise ModelError('transport_start_failed', diagnostic=['phase_ipc', 'worker_start'])
        try:
            if not use_ipc:
                result = json.loads(proc.stdout)
                if proc.returncode != 0:
                    raise ValueError()
            if not isinstance(result, dict):
                raise ValueError()
            if 'transport_error' in result:
                code = result['transport_error']
                allowed = {'rate_limited', 'service_unavailable', 'authentication_failed', 'http_error',
                           'response_too_large', 'transport_unknown', 'tls_configuration_error', 'tls_verification_failed', 'dns_failed'}
                if code not in allowed:
                    raise ValueError()
                raise ModelError(code, code in ('rate_limited', 'service_unavailable'),
                                 code in ('response_too_large', 'transport_unknown'),
                                 http_status=result.get('http_status'), diagnostic=result.get('diagnostic'),
                                 response_body_bytes=result.get('response_body_bytes'))
            canonical = result['completion']
            if len(json.dumps(canonical, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_CANONICAL:
                raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
            if not isinstance(canonical, dict) or not isinstance(canonical['message'], dict):
                raise ValueError()
            request_id = canonical.get('request_id')
            return {'message': canonical['message'], 'usage': canonical.get('usage'),
                    'finish_reason': canonical.get('finish_reason'),
                    'request_id_hash': hashlib.sha256(request_id.encode()).hexdigest() if isinstance(request_id, str) else None}
        except ModelError:
            raise
        except (ValueError, KeyError, IndexError, TypeError):
            metadata = result if isinstance(result, dict) else {}
            diagnostic = normalize_diagnostics(metadata.get('diagnostic'))
            if not diagnostic:
                diagnostic = ['phase_ipc', 'worker_exit' if not use_ipc and proc.returncode != 0 else 'worker_framing']
            raise ModelError('invalid_model_response', uncertain=True, diagnostic=diagnostic,
                             http_status=metadata.get('http_status'),
                             response_body_bytes=metadata.get('response_body_bytes'))

    def _stream_worker(self, serialized, environment, timeout):
        with _owner_pipe(environment) as (owner_fd, owned_environment):
            return self._stream_worker_owned(serialized, owned_environment, timeout, owner_fd)

    def _stream_worker_owned(self, serialized, environment, timeout, owner_fd):
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ModelError('invalid_model_request')
        request = serialized.encode('utf-8')
        if len(request) > MAX_STREAM_REQUEST:
            raise ModelError('invalid_model_request')
        deadline = time.monotonic() + timeout
        try:
            proc = subprocess.Popen([sys.executable, '-I', '-S', str(Path(__file__).resolve())],
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=environment, start_new_session=True, close_fds=True, pass_fds=(owner_fd,), bufsize=0)
        except OSError:
            raise ModelError('transport_start_failed', diagnostic=['phase_ipc', 'worker_start']) from None
        buffer, sent, received, stderr_bytes = bytearray(), 0, 0, 0
        final, text_bytes, displayed_bytes = _PENDING, 0, 0
        displayed_hash, callback_failed = hashlib.sha256(), False
        try:
            with selectors.DefaultSelector() as selector:
                for pipe, events, label in ((proc.stdin, selectors.EVENT_WRITE, 'stdin'),
                                            (proc.stdout, selectors.EVENT_READ, 'stdout'),
                                            (proc.stderr, selectors.EVENT_READ, 'stderr')):
                    os.set_blocking(pipe.fileno(), False)
                    selector.register(pipe, events, label)
                while selector.get_map():
                    if sent == len(request):
                        self._check_cancelled()
                    if time.monotonic() >= deadline:
                        raise ModelError('model_timeout', uncertain=True, diagnostic=['phase_ipc', 'deadline'])
                    wait = max(0, deadline-time.monotonic())
                    if self._cancellation_checks.get():
                        wait = min(.05, wait)
                    for key, _ in selector.select(wait):
                        if key.data == 'stdin':
                            try:
                                sent += os.write(key.fd, request[sent:sent+65536])
                            except BlockingIOError:
                                continue
                            if sent == len(request):
                                selector.unregister(key.fileobj)
                                proc.stdin.close()
                            continue
                        try:
                            chunk = os.read(key.fd, 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        received += len(chunk)
                        if received > MAX_WORKER_OUTPUT:
                            raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
                        if key.data == 'stderr':
                            stderr_bytes += len(chunk)
                            if stderr_bytes > MAX_WORKER_STDERR:
                                raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
                            continue  # Never expose worker stderr or reflected provider text.
                        buffer.extend(chunk)
                        while b'\n' in buffer:
                            if time.monotonic() >= deadline:
                                raise ModelError('model_timeout', uncertain=True, diagnostic=['phase_ipc', 'deadline'])
                            line, _, tail = buffer.partition(b'\n')
                            buffer = bytearray(tail)
                            if len(line) > MAX_IPC_FRAME:
                                raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
                            try:
                                frame = json.loads(line.decode('utf-8'))
                            except (ValueError, UnicodeError):
                                raise ModelError('invalid_model_response', uncertain=True,
                                                 diagnostic=['phase_ipc', 'worker_framing']) from None
                            if not isinstance(frame, dict) or final is not _PENDING:
                                raise ModelError('invalid_model_response', uncertain=True,
                                                 diagnostic=['phase_ipc', 'worker_framing'])
                            if frame.get('type') == 'text_delta' and set(frame) == {'type', 'text'} and isinstance(frame['text'], str):
                                encoded = frame['text'].encode('utf-8')
                                text_bytes += len(encoded)
                                if text_bytes > MAX_RESPONSE:
                                    raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
                                if self.on_text is not None and not callback_failed:
                                    try:
                                        self.on_text(frame['text'])
                                    except Exception:
                                        callback_failed = True
                                    else:
                                        displayed_hash.update(encoded)
                                        displayed_bytes += len(encoded)
                            elif frame.get('type') == 'final' and set(frame) == {'type', 'result'} and isinstance(frame['result'], dict):
                                final = frame['result']
                            else:
                                raise ModelError('invalid_model_response', uncertain=True,
                                                 diagnostic=['phase_ipc', 'worker_framing'])
                        if len(buffer) > MAX_IPC_FRAME:
                            raise ModelError('response_too_large', uncertain=True, diagnostic=['phase_ipc', 'size_limit'])
                if time.monotonic() >= deadline:
                    raise ModelError('model_timeout', uncertain=True, diagnostic=['phase_ipc', 'deadline'])
                try:
                    code = proc.wait(timeout=max(.001, deadline-time.monotonic()))
                except subprocess.TimeoutExpired:
                    raise ModelError('model_timeout', uncertain=True, diagnostic=['phase_ipc', 'deadline']) from None
                if code != 0 or buffer or final is _PENDING:
                    family = 'worker_exit' if code != 0 else ('worker_framing' if buffer else 'worker_no_final')
                    raise ModelError('invalid_model_response', uncertain=True, diagnostic=['phase_ipc', family])
                text = final.get('completion', {}).get('message', {}).get('content')
                if self.on_text is not None and not callback_failed and isinstance(text, str) and displayed_bytes:
                    encoded = text.encode('utf-8')
                    self.last_text_streamed = (displayed_bytes == len(encoded) and displayed_hash.digest() == hashlib.sha256(encoded).digest())
                return final
        except (OSError, ValueError, TypeError, UnicodeError) as error:
            raise ModelError('invalid_model_response', uncertain=True,
                             diagnostic=['phase_ipc', 'io' if isinstance(error, OSError) else 'worker_framing']) from None
        finally:
            # poll reaps an exited leader before group signalling (macOS zombie
            # groups can otherwise return EPERM). No transport child may survive.
            proc.poll()
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                if proc.poll() is None:
                    proc.kill()
            proc.wait()
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if not pipe.closed:
                    pipe.close()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _ResponseTooLarge(Exception):
    pass


def _sse_lines(response, progress=None):
    """Read available bytes, not full HTTP blocks; handle split UTF-8/CRLF."""
    decoder, pending, after_cr, total = codecs.getincrementaldecoder('utf-8-sig')(), '', False, 0
    while True:
        chunk = response.read1(8192)
        total += len(chunk)
        if progress is not None:
            progress['response_body_bytes'] = total
        if total > MAX_RESPONSE:
            raise _ResponseTooLarge()
        pending += decoder.decode(chunk, final=not chunk)
        if after_cr and pending:
            if pending.startswith('\n'):
                pending = pending[1:]
            after_cr = False
        while True:
            cr, lf = pending.find('\r'), pending.find('\n')
            positions = [position for position in (cr, lf) if position >= 0]
            if not positions:
                break
            position = min(positions)
            line, delimiter = pending[:position], pending[position]
            pending = pending[position+1:]
            if delimiter == '\r':
                if pending.startswith('\n'):
                    pending = pending[1:]
                elif not pending:
                    after_cr = True
            yield line + '\n'
        if not chunk:
            if pending:
                yield pending
            return


def _failure_family(error, chunked=False):
    # Inspect exception classes only. A reason can itself be an exception or an
    # arbitrary string containing reflected headers, credentials, or body text.
    if isinstance(error, urllib.error.URLError) and isinstance(error.reason, BaseException):
        error = error.reason
    if isinstance(error, http.client.IncompleteRead):
        return 'incomplete_chunk' if chunked else 'incomplete_read'
    if isinstance(error, http.client.RemoteDisconnected):
        return 'peer_closed'
    if isinstance(error, ConnectionResetError):
        return 'connection_reset'
    if isinstance(error, ssl.SSLCertVerificationError):
        return 'tls_certificate'
    if isinstance(error, ssl.SSLEOFError):
        return 'tls_eof'
    if isinstance(error, ssl.SSLError):
        return 'tls'
    if isinstance(error, socket.gaierror):
        return 'dns'
    if isinstance(error, TimeoutError):
        return 'timeout'
    if isinstance(error, ConnectionError):
        return 'network'
    if isinstance(error, http.client.HTTPException):
        return 'http_framing'
    if isinstance(error, OSError) and not isinstance(error, urllib.error.URLError):
        return 'io'
    return 'unknown'


def _failure_details(phase, family, http_status=None, progress=None):
    details = {'diagnostic': [phase, family]}
    if type(http_status) is int and 100 <= http_status <= 599:
        details['http_status'] = http_status
    if progress is not None:
        details['response_body_bytes'] = progress['response_body_bytes']
    return details


def _protocol_detail(error):
    # Never stringify exceptions or forward provider-controlled error fields.
    if type(error) is _protocols.ProtocolError and len(error.args) == 1:
        code = error.args[0]
        if type(code) is str and code in PROTOCOL_DIAGNOSTIC_LABELS:
            return code
    if isinstance(error, json.JSONDecodeError):
        return 'json_parse'
    return None


def transport(payload, on_text=None):
    protocol = payload['protocol']
    headers = {'Content-Type': 'application/json', 'Accept': 'text/event-stream' if payload['stream'] else 'application/json'}
    if protocol == 'anthropic':
        headers.update({'x-api-key': os.environ['AGENT_LAB_TRANSPORT_KEY'], 'anthropic-version': '2023-06-01'})
    else:
        headers['Authorization'] = 'Bearer ' + os.environ['AGENT_LAB_TRANSPORT_KEY']
    request = urllib.request.Request(payload['url'], data=json.dumps(payload['body'], allow_nan=False).encode(), headers=headers)
    phase, http_status, progress = 'phase_tls_setup', None, None
    try:
        # No ambient proxies or redirects: credentials go only to the configured endpoint.
        try:
            context = ssl.create_default_context(cafile=payload.get('ca_bundle'))
        except (OSError, ssl.SSLError):
            return {'transport_error': 'tls_configuration_error', **_failure_details(phase, 'tls')}
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                             urllib.request.HTTPSHandler(context=context))
        phase = 'phase_open'
        with opener.open(request, timeout=payload['timeout']) as response:
            phase = 'phase_headers'
            http_status = getattr(response, 'status', None)
            header_id = response.headers.get('x-request-id') or response.headers.get('request-id')
            try:
                phase = 'phase_body'
                progress = {'response_body_bytes': 0, 'chunked': getattr(response, 'chunked', False) is True}
                if payload['stream']:
                    canonical = _protocols.consume_sse(protocol, _sse_lines(response, progress), on_text=on_text)
                else:
                    raw = response.read(MAX_RESPONSE + 1)
                    progress['response_body_bytes'] = len(raw)
                    if len(raw) > MAX_RESPONSE:
                        raise _ResponseTooLarge()
                    canonical = _protocols.normalize_response(protocol, json.loads(raw))
                if canonical['request_id'] is None and isinstance(header_id, str):
                    canonical['request_id'] = header_id
                if len(json.dumps(canonical, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_CANONICAL:
                    raise _ResponseTooLarge()
                return {'completion': canonical}
            except _ResponseTooLarge:
                return {'transport_error': 'response_too_large', **_failure_details(phase, 'size_limit', http_status, progress)}
            except (ValueError, TypeError, KeyError, UnicodeError) as error:
                details = _failure_details(phase, 'protocol', http_status, progress)
                detail = _protocol_detail(error)
                if detail is not None:
                    details['diagnostic'].append(detail)
                return {'invalid_response': True, **details}
    except urllib.error.HTTPError as error:
        if error.code in (429, 503):
            return {'transport_error': 'rate_limited' if error.code == 429 else 'service_unavailable',
                    **_failure_details('phase_open', 'http_status', error.code)}
        # Fixed vocabulary only: never return arbitrary provider text, which may
        # reflect secrets or request bodies. Keep status for actionable diagnosis.
        details = _failure_details('phase_body', 'http_status', error.code)
        try:
            raw = error.read(65536)
            details['response_body_bytes'] = len(raw)
            detail = raw.decode('utf-8', errors='replace').lower()
        except Exception as body_error:
            detail = ''
            details['diagnostic'].append(_failure_family(body_error))
        details['diagnostic'].extend(label for label in HTTP_DIAGNOSTIC_LABELS if label in detail)
        return {'transport_error': 'authentication_failed' if error.code in (401, 403) else 'http_error',
                **details}
    except urllib.error.URLError as error:
        details = _failure_details(phase, _failure_family(error, progress is not None and progress['chunked']), http_status, progress)
        if isinstance(error.reason, ssl.SSLCertVerificationError):
            return {'transport_error': 'tls_verification_failed', **details}
        if isinstance(error.reason, socket.gaierror):
            return {'transport_error': 'dns_failed', **details}
        return {'transport_error': 'transport_unknown', **details}
    except Exception as error:
        return {'transport_error': 'transport_unknown',
                **_failure_details(phase, _failure_family(error, progress is not None and progress['chunked']), http_status, progress)}


if __name__ == '__main__':
    _watch_owner()
    streaming = False
    emitted = 0
    def emit(value):
        global emitted
        packet = (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode('utf-8')
        if len(packet) > MAX_IPC_FRAME or emitted + len(packet) > MAX_WORKER_OUTPUT:
            raise _protocols.ProtocolError('worker_output_limit')
        emitted += len(packet)
        sys.stdout.buffer.write(packet)
        sys.stdout.buffer.flush()
    try:
        payload = json.loads(sys.stdin.read())
        streaming = payload.get('stream') is True or payload.get('ipc_stream') is True
        result = transport(payload, on_text=(lambda text: emit({'type': 'text_delta', 'text': text})) if payload.get('stream') is True else None)
        if streaming:
            emit({'type': 'final', 'result': result})
        else:
            print(json.dumps(result, allow_nan=False))
    except Exception:
        if streaming:
            try:
                emit({'type': 'final', 'result': {'transport_error': 'transport_unknown',
                                                'diagnostic': ['phase_ipc', 'worker_failure']}})
            except Exception:
                pass
        else:
            print(json.dumps({'transport_error': 'transport_unknown', 'diagnostic': ['phase_ipc', 'worker_failure']}))
