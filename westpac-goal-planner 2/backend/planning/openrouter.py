"""OpenRouter wire adapter for the existing explicit, read-only planner loop.

Google request objects remain the internal declaration format to preserve existing callers.
Native OpenRouter assistant messages are opaque: checkpoint/replay them without translating
or discarding reasoning fields. There are no HTTP retries or sleeps in this adapter.
"""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import re
import time
from types import SimpleNamespace
from urllib.parse import quote, urlparse

import httpx
from dotenv import load_dotenv
from google.genai import types

from .gemini import PlannerError, retry_after


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    def constant(_):
        raise ValueError('Nonfinite JSON value')
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    # JSON exponents can overflow to infinity without parse_constant being called.
    json.dumps(value, allow_nan=False)
    return value


@dataclass
class NativeMessage:
    message: dict
    route: str

    def model_dump(self, **_):
        return {'openrouter_message': deepcopy(self.message), 'route': self.route}


class OpenRouterResponse:
    """Only the response properties consumed by the existing planner, plus lossless state."""
    def __init__(self, payload, route):
        self.payload = deepcopy(payload)
        self.route = route
        choices = payload.get('choices')
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError('Expected one completion')
        choice = choices[0]
        message = choice.get('message')
        if not isinstance(message, dict) or message.get('role') != 'assistant':
            raise ValueError('Expected assistant message')
        if choice.get('finish_reason') not in {'stop', 'tool_calls'} or message.get('refusal'):
            raise ValueError('Incomplete or refused completion')
        content = message.get('content')
        if content is not None and not isinstance(content, str):
            raise ValueError('Unsupported content')
        self.text = content or ''
        self.function_calls = []
        calls = message.get('tool_calls') or []
        if not isinstance(calls, list) or len(calls) > 8:
            raise ValueError('Invalid tool batch')
        seen = set()
        for call in calls:
            call_id = call.get('id')
            fn = call.get('function')
            if (not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', call_id)
                    or call_id in seen or call.get('type') != 'function' or not isinstance(fn, dict)
                    or not isinstance(fn.get('name'), str) or not isinstance(fn.get('arguments'), str)):
                raise ValueError('Invalid tool call identity')
            seen.add(call_id)
            # Malformed JSON is a protocol error, never a partially executed tool batch.
            args = strict_json(fn['arguments'])
            if not isinstance(args, dict):
                raise ValueError('Expected argument object')
            self.function_calls.append(SimpleNamespace(id=call_id, name=fn['name'], args=args))
        if not self.function_calls and not self.text.strip():
            raise ValueError('Empty completion')
        if choice['finish_reason'] == 'tool_calls' and not self.function_calls:
            raise ValueError('Missing tool calls')
        self.candidates = [SimpleNamespace(content=NativeMessage(deepcopy(message), route))]
        usage = payload.get('usage') or {}
        if not isinstance(usage, dict):
            raise ValueError('Invalid usage')
        def count(name, source=usage):
            value = source.get(name, 0)
            if type(value) is not int or value < 0:
                raise ValueError('Invalid token count')
            return value
        detail = usage.get('completion_tokens_details') or {}
        if not isinstance(detail, dict):
            raise ValueError('Invalid reasoning usage')
        cost = usage.get('cost')
        if cost is not None and (isinstance(cost, bool) or not isinstance(cost, (int, float))
                                 or not math.isfinite(cost) or cost < 0):
            raise ValueError('Invalid cost')
        self.usage_metadata = SimpleNamespace(prompt_token_count=count('prompt_tokens'),
            candidates_token_count=count('completion_tokens'), total_token_count=count('total_tokens'),
            thoughts_token_count=count('reasoning_tokens', detail), cost=cost) if usage else None
        self.response_id = payload.get('id')
        self.model_version = payload.get('model')

    def model_dump(self, **_):
        return {'openrouter_response': deepcopy(self.payload), 'route': self.route}


class OpenRouterTransport:
    provider = 'openrouter'
    min_request_interval = 5.0

    def __init__(self, env_file=Path(__file__).resolve().parents[3]/'.env', *, client=None):
        if env_file is not None:
            load_dotenv(env_file, override=False)
        self.model = os.getenv('OPENROUTER_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b:free')
        self.base_url = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')
        self.profile = os.getenv('OPENROUTER_PROFILE', 'tools_json_text')
        self.final_output_mode = {'tools_json_text': 'json_text', 'tools_structured': 'structured'}.get(self.profile)
        self.available = bool(os.getenv('OPENROUTER_API_KEY'))
        self.reasoning_effort = os.getenv('OPENROUTER_REASONING_EFFORT','low')
        if self.reasoning_effort not in {'none','minimal','low','medium','high'}:
            raise PlannerError('planner_configuration','Invalid OPENROUTER_REASONING_EFFORT.',503)
        self.allow_paid = os.getenv('OPENROUTER_ALLOW_PAID', 'false').lower() == 'true'
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+', self.model)
                or self.model.startswith('openrouter/') or not self.final_output_mode
                or urlparse(self.base_url).scheme != 'https'
                or urlparse(self.base_url).netloc != 'openrouter.ai'
                or urlparse(self.base_url).path != '/api/v1'
                or urlparse(self.base_url).query or urlparse(self.base_url).fragment):
            raise PlannerError('planner_configuration', 'Invalid OpenRouter model, profile or base URL.', 503)
        if not self.allow_paid and not self.model.endswith(':free'):
            raise PlannerError('planner_configuration', 'Paid OpenRouter models require explicit OPENROUTER_ALLOW_PAID=true.', 503)
        self.client = client or httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False)
        self.endpoint = None
        self.last_request_started = None

    @property
    def checkpoint_identity(self):
        return {'provider': self.provider, 'model': self.model, 'profile': self.profile,
                'base_url': self.base_url, 'allow_paid': self.allow_paid, 'reasoning_effort':self.reasoning_effort}

    def restore_content(self, content):
        if 'openrouter_message' in content:
            return NativeMessage(deepcopy(content['openrouter_message']), content['route'])
        return types.Content.model_validate(content)

    def restore_response(self, saved):
        return OpenRouterResponse(saved['openrouter_response'], saved['route'])

    def _endpoint(self, timeout, route=None):
        if self.endpoint and (route is None or self.endpoint['tag'] == route):
            return self.endpoint
        # Public metadata only, no credential, inference or automatic fallback.
        response = self.client.get(f'{self.base_url}/models/{quote(self.model, safe="/")}/endpoints', timeout=timeout)
        if response.status_code != 200:
            raise PlannerError('planner_configuration', 'Could not verify OpenRouter endpoint capabilities.', 503)
        data = strict_json(response.text)
        endpoints = data.get('data', {}).get('endpoints', [])
        for endpoint in endpoints:
            if endpoint.get('status') not in {None,0}:continue
            params = set(endpoint.get('supported_parameters') or [])
            choices = endpoint.get('supports_tool_choice') or {}
            required = {'tools', 'tool_choice', 'max_tokens'}
            if self.final_output_mode == 'structured':
                required |= {'response_format', 'structured_outputs'}
            if not required <= params or not all(choices.get(k) is True for k in ('none', 'auto', 'required')):
                continue
            tag = endpoint.get('tag')
            if not isinstance(tag, str) or (route and tag != route):
                continue
            pricing = endpoint.get('pricing') or {}
            if not self.allow_paid and (str(pricing.get('prompt')) != '0' or str(pricing.get('completion')) != '0'):
                continue
            if not isinstance(endpoint.get('context_length'), int) or not isinstance(endpoint.get('max_completion_tokens'), int):
                continue
            self.endpoint = endpoint
            return endpoint
        raise PlannerError('planner_configuration', 'No OpenRouter endpoint supports the required tools, tool choices, output profile and price policy.', 503)

    def _messages(self, contents):
        messages = []
        route = None
        outstanding = set()
        for content in contents:
            if isinstance(content, NativeMessage):
                if route and route != content.route:
                    raise ValueError('Mixed routes')
                route = content.route
                if outstanding:
                    raise ValueError('Unanswered tool calls')
                native = deepcopy(content.message)
                outstanding = {c['id'] for c in native.get('tool_calls') or []}
                messages.append(native)
            else:
                texts = []
                for part in content.parts or []:
                    if part.function_response:
                        result = part.function_response
                        if result.id not in outstanding:
                            raise ValueError('Unmatched tool result')
                        outstanding.remove(result.id)
                        messages.append({'role': 'tool', 'tool_call_id': result.id,
                                         'content': json.dumps(result.response, allow_nan=False)})
                    elif part.text:
                        texts.append(part.text)
                    else:
                        raise ValueError('Unsupported history part')
                if texts:
                    if outstanding:
                        raise ValueError('Unanswered tool calls')
                    messages.append({'role': 'assistant' if content.role == 'model' else 'user', 'content': '\n'.join(texts)})
        if outstanding:
            raise ValueError('Unanswered tool calls')
        return messages, route

    def _error(self, response, body):
        error = body.get('error', {}) if isinstance(body, dict) else {}
        if not isinstance(error, dict):error = {}
        status = response.status_code if response.status_code >= 400 else error.get('code', 502)
        delay = retry_after(response)
        # Inspect, but never persist/log provider free-form error text or raw metadata.
        evidence = json.dumps(error).lower()
        daily = bool(re.search(r'daily|per.day|requests.per.day|quota.exhausted|insufficient.credit|zero.quota|limit[:\s]+0\b', evidence))
        transient = bool(re.search(r'per.minute|per.second|temporar|rate.limit|too many requests|overload|capacity', evidence))
        if status == 429:
            retryable = not daily and (delay is not None or transient)
            raise PlannerError('planner_rate_limited', 'OpenRouter rate or quota limit reached. Completed calculations are preserved.',
                429, upstream_status=429, retry_after_seconds=delay, retryable=retryable,
                resume_allowed=not daily, provider_details={'classification': 'daily_or_exhausted_quota' if daily else 'temporary_rate_limit' if retryable else 'unknown_quota'})
        if status in {400, 401, 402, 403, 404, 422}:
            raise PlannerError('planner_configuration', 'OpenRouter rejected the credentials, model, budget or request capabilities.',
                               503, upstream_status=status, retryable=False, resume_allowed=False)
        if status in {408, 504}:
            raise PlannerError('planner_timeout', 'OpenRouter timed out. Completed work can be resumed explicitly.',
                               504, upstream_status=status, retry_after_seconds=delay, retryable=True, resume_allowed=True)
        raise PlannerError('planner_provider_error', 'OpenRouter could not complete the generation. No recommendation was substituted.',
                           502, upstream_status=status, retry_after_seconds=delay, retryable=status in {502, 503}, resume_allowed=status in {502, 503})

    def generate(self, contents, config, timeout_ms):
        if not self.available:
            raise PlannerError('planner_unconfigured', 'Configure OPENROUTER_API_KEY on the backend.', 503)
        started = time.monotonic()
        self.request_start_offset=0
        try:
            messages, route = self._messages(contents)
            endpoint = self._endpoint(timeout_ms/1000, route)
            body = {'model': self.model, 'messages': [{'role': 'system', 'content': config.system_instruction}] + messages,
                    'max_tokens': config.max_output_tokens, 'stream': False,
                    'provider': {'only': [endpoint['tag']], 'allow_fallbacks': False, 'require_parameters': True}}
            if 'temperature' in endpoint['supported_parameters']:
                body['temperature'] = config.temperature
            if 'reasoning_effort' in endpoint['supported_parameters']:
                body['reasoning']={'effort':self.reasoning_effort}
            mode = config.tool_config.function_calling_config.mode
            body['tool_choice'] = {'ANY': 'required', 'AUTO': 'auto', 'NONE': 'none'}[mode]
            if config.tools:
                body['tools'] = [{'type': 'function', 'function': {'name': d.name, 'description': d.description,
                    'parameters': d.parameters_json_schema}} for tool in config.tools for d in tool.function_declarations]
            if config.response_json_schema:
                if self.final_output_mode != 'structured':
                    raise PlannerError('planner_configuration', 'This OpenRouter profile requires JSON-text final output.', 503)
                body['response_format'] = {'type': 'json_schema', 'json_schema': {
                    'name': 'planner_result', 'strict': True, 'schema': config.response_json_schema}}
            # Conservative byte upper bound, including tools/schema/reasoning, without truncation.
            input_upper_bound = len(json.dumps(body, ensure_ascii=False).encode('utf-8'))
            if (input_upper_bound + config.max_output_tokens > endpoint['context_length']
                    or config.max_output_tokens > endpoint['max_completion_tokens']):
                raise PlannerError('planner_limit', 'OpenRouter context/output capacity is insufficient; no evidence was omitted.', 413)
            remaining = timeout_ms/1000 - (time.monotonic()-started)
            if remaining <= 0:raise TimeoutError()
            self.request_start_offset=time.monotonic()-started
            response = self.client.post(f'{self.base_url}/chat/completions', json=body,
                headers={'Authorization': f'Bearer {os.environ["OPENROUTER_API_KEY"]}'}, timeout=remaining)
            try:payload = strict_json(response.text)
            except ValueError:
                if response.status_code>=400:self._error(response,{})
                raise
            if response.status_code >= 400 or isinstance(payload, dict) and payload.get('error'):
                self._error(response, payload)
            if not isinstance(payload, dict):raise ValueError('Invalid response')
            choices=payload.get('choices')
            if isinstance(choices,list) and choices and isinstance(choices[0],dict) and choices[0].get('finish_reason')=='error':
                self._error(response,{'error':choices[0].get('error') or {'code':502}})
            result = OpenRouterResponse(payload, endpoint['tag'])
            old_ids={c['id'] for m in messages for c in m.get('tool_calls') or []}
            if any(c.id in old_ids for c in result.function_calls):raise ValueError('Reused tool call ID')
            if body['tool_choice'] == 'none' and result.function_calls:
                raise ValueError('Tools disabled')
            if body['tool_choice'] == 'required' and not result.function_calls:
                raise ValueError('Required tool missing')
            return result
        except PlannerError:
            raise
        except (httpx.TimeoutException, TimeoutError):
            raise PlannerError('planner_timeout', 'OpenRouter timed out. Completed work can be resumed explicitly.',
                               504, retryable=False, resume_allowed=True) from None
        except httpx.TransportError:
            raise PlannerError('planner_provider_error', 'OpenRouter connection failed. Resume explicitly to avoid duplicate requests.',
                               502, retryable=False, resume_allowed=True) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise PlannerError('planner_invalid_response', 'OpenRouter returned an invalid or unsupported response. No draft accepted.') from None
