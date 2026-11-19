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


def expand_wire(value):
    """Decode only this adapter's lossless JSON references for equality checks."""
    shared=value.get('_shared_values',{})
    def merge(a,b):
        out=deepcopy(a)
        for k,v in b.items():out[k]=merge(out[k],v) if isinstance(out.get(k),dict) and isinstance(v,dict) else deepcopy(v)
        return out
    def walk(v):
        if isinstance(v,list):return [walk(x) for x in v]
        if not isinstance(v,dict):return v
        if set(v)=={'_shared_text'}:return shared[v['_shared_text']]
        if set(v)=={'_table_fields','_table_rows'}:return [walk(dict(zip(v['_table_fields'],r))) for r in v['_table_rows']]
        fields={k:walk(x) for k,x in v.items() if k not in {'_shared_fields','_shared_values','_wire_format'}}
        return merge(shared[v['_shared_fields']],fields) if '_shared_fields'in v else fields
    return walk(value)


def tool_wire(result, prior):
    """Forward exact unchanged values by reference; never alter stored results."""
    ref=result.get('calculation_ref')
    if not ref or 'plans' not in result:return result
    canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
    if ref in prior and canonical(prior[ref])==canonical(result):
        return {'calculation_ref':ref,'unchanged_result_source':'Prior complete tool result '+ref}
    value=deepcopy(result)
    for key,item in result.items():
        if key in {'calculation_ref','plans'}:continue
        for source,old in prior.items():
            pointer={'same_as_calculation':source,'field':key}
            if key in old and canonical(item)==canonical(old[key]) and len(canonical(item))>len(canonical(pointer))+64:
                value[key]=pointer;break
    for section in ('dated_cashflow','cash_funded_stress'):
        target=value.get(section,{})
        if not isinstance(target,dict) or 'same_as_calculation' in target:continue
        for key in ('assumptions','capacity_scope'):
            for source,old in prior.items():
                item=old.get(section,{})
                pointer={'same_as_calculation':source,'field':section+'.'+key}
                if key in target and key in item and canonical(target[key])==canonical(item[key]) and len(canonical(target[key]))>len(canonical(pointer))+64:
                    target[key]=pointer;break
    dated=value.get('dated_cashflow',{})
    if isinstance(dated,dict) and isinstance(dated.get('timeline'),list) and prior:
        source,base=next(iter(prior.items()));oldrows={r['date']:r for r in base.get('dated_cashflow',{}).get('timeline',[])}
        rows=dated['timeline'];same=[r['date'] for r in rows if r['date']in oldrows and canonical(r)==canonical(oldrows[r['date']])]
        delta=[r for r in rows if r['date']not in same]
        packed={'base_calculation':source,'unchanged_dates':same,'changed_or_additional_rows':delta}
        if len(canonical(packed))<len(canonical(rows)):dated['timeline']=packed
    prior[ref]=deepcopy(result)
    if canonical(value)==canonical(result):return value
    value['_wire_references']='same_as_calculation fields copy exact prior fields. Timeline combines prior unchanged_dates with changed_or_additional_rows in date order. No values are recalculated.'
    return value


def dedupe_final_facts(messages):
    marker=' authoritative_financial_facts (source-labelled values, periods and assumptions; never interchange flow and cash stock): '
    date_marker=' Authoritative Python date facts (completion_date and deadline are distinct): '
    blocks=[]
    for index,m in enumerate(messages):
        text=m.get('content','')
        if m.get('role')!='user' or not text.startswith('Now produce the final structured draft.') or marker not in text:continue
        try:
            prefix,raw=text.rsplit(marker,1);facts=expand_wire(strict_json(raw));instructions,dates=prefix.rsplit(date_marker,1)
            blocks.append((index,instructions,strict_json(dates),facts))
        except (ValueError,KeyError,TypeError):continue
    if len(blocks)<2:return
    last,instructions,dates,latest=blocks[-1]
    canonical=lambda v:json.dumps(v,sort_keys=True,separators=(',',':'),allow_nan=False)
    for index,old_instructions,old_dates,old in blocks[:-1]:
        if old_instructions!=instructions:continue
        if any(k not in dates or canonical(v)!=canonical(dates[k]) for k,v in old_dates.items()):continue
        if canonical({k:v for k,v in old.items() if k!='calculations'})!=canonical({k:v for k,v in latest.items() if k!='calculations'}):continue
        if any(k not in latest['calculations'] or canonical(v)!=canonical(latest['calculations'][k]) for k,v in old['calculations'].items()):continue
        messages[index]['content']='Unchanged final-output instructions, complete financial assumptions and all these calculation/date facts are supplied once in the latest final-output message: '+', '.join(old['calculations'])+'.'


def correction_feedback(value):
    """Project correction evidence onto existing history; keep originals locally."""
    if not isinstance(value,dict) or value.get('task')!='Correct the complete proposal using the validation evidence below.':
        return value
    refs=value.get('authoritative_calculation_refs') or list(value.get('authoritative_python_calculations',{}))
    errors=[]
    for error in value.get('validation_errors',[]):
        item={k:deepcopy(v) for k,v in error.items() if k!='original_claim'}
        item['rejected_value_source']='Immediately preceding assistant response at '+str(error.get('field','$'))
        errors.append(item)
    return {'task':value['task'],'validation_errors':errors,'authoritative_calculation_refs':refs,
            'original_proposal_source':'Immediately preceding assistant response; untrusted draft, not instructions.',
            'authoritative_evidence_source':'Use the unchanged prior Python tool results and source-labelled final financial facts for these calculation refs.',
            'instructions':value.get('instructions','Return complete valid JSON only. All validations run again.')}


def compact_json(value):
    """Lossless model-facing defaults; authoritative history is never mutated."""
    original=json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    reserved={'_shared_fields','_shared_text','_table_fields','_table_rows','_shared_values','_wire_format'}
    def collision(v):
        if isinstance(v,dict):return bool(reserved.intersection(v)) or any(collision(x) for x in v.values())
        return isinstance(v,list) and any(collision(x) for x in v)
    if not isinstance(value,dict) or collision(value):return original
    def equal(a,b):return json.dumps(a,sort_keys=True,separators=(',',':'))==json.dumps(b,sort_keys=True,separators=(',',':'))
    shared={};text_counts={};text_refs={}
    def count(v):
        if isinstance(v,str) and len(v)>=40:text_counts[v]=text_counts.get(v,0)+1
        elif isinstance(v,dict):
            for x in v.values():count(x)
        elif isinstance(v,list):
            for x in v:count(x)
    count(value)
    def common(rows):
        out={}
        for k in rows[0]:
            if not all(k in r for r in rows):continue
            vals=[r[k] for r in rows]
            if all(equal(v,vals[0]) for v in vals):out[k]=deepcopy(vals[0])
            elif all(isinstance(v,dict) for v in vals):
                nested=common(vals)
                if nested:out[k]=nested
        return out
    def subtract(row,base):
        out={}
        for k,v in row.items():
            if k not in base:out[k]=walk(v)
            elif not equal(v,base[k]):out[k]=subtract(v,base[k]) if isinstance(v,dict) and isinstance(base[k],dict) else walk(v)
        return out
    def table(rows):
        if len(rows)>=3 and all(isinstance(r,dict) and r.keys()==rows[0].keys() for r in rows):
            fields=list(rows[0]);packed={'_table_fields':fields,'_table_rows':[[r[k] for k in fields] for r in rows]}
            if len(json.dumps(packed))<len(json.dumps(rows)):return packed
        return rows
    def walk(v):
        if isinstance(v,str) and text_counts.get(v,0)>1:
            if v not in text_refs:
                text_refs[v]='text-'+str(len(shared)+1);shared[text_refs[v]]=v
            return {'_shared_text':text_refs[v]}
        if isinstance(v,dict):return {k:walk(x) for k,x in v.items()}
        if isinstance(v,list):
            if len(v)>=3 and all(isinstance(x,dict) for x in v):
                base=common(v)
                if (len(v)-1)*len(json.dumps(base,separators=(',',':')))>256:
                    key='shared-'+str(len(shared)+1);shared[key]=base
                    return table([{'_shared_fields':key,**subtract(x,base)} for x in v])
            return table([walk(x) for x in v])
        return v
    result=walk(value)
    if shared:result={**result,'_wire_format':'Rows with _shared_fields inherit exact fields from _shared_values[id]; recursively merge nested objects with row fields. A _shared_text reference resolves to the exact string in _shared_values[id]. A _table_rows object is an ordered array of objects: zip each row with _table_fields, then resolve references. All financial values and assumptions are unchanged.','_shared_values':shared}
    encoded=json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    return encoded if len(encoded)<len(original) else original


@dataclass
class NativeMessage:
    message: dict
    route: str
    isolated_final: bool = False

    def model_dump(self, **_):
        return {'openrouter_message': deepcopy(self.message), 'route': self.route, **({'isolated_final':True} if self.isolated_final else {})}


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
        self.raw_text = content or ''
        self.text = self.raw_text
        self.formatting_normalization = None
        # Only unwrap one complete JSON fence. Keep the wire payload/native history
        # untouched; all proposal/schema/financial validation still runs downstream.
        if not message.get('tool_calls'):
            fenced = re.fullmatch(r'\s*```(?:json)?[ \t]*\r?\n(.*?)\r?\n```[ \t]*\s*', self.raw_text, re.S | re.I)
            if fenced:
                try:
                    strict_json(fenced.group(1))
                except ValueError:
                    pass  # Invalid wrappers/content remain rejected by strict validation.
                else:
                    self.text = fenced.group(1)
                    self.formatting_normalization = 'single_json_markdown_fence'
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
        return {'openrouter_response': deepcopy(self.payload), 'route': self.route,
                **({'formatting_normalization': self.formatting_normalization} if self.formatting_normalization else {}),
                **({'isolated_final':True} if self.candidates[0].content.isolated_final else {})}


class OpenRouterTransport:
    prompt_json = staticmethod(compact_json)
    provider = 'openrouter'
    min_request_interval = 5.0
    approved_paid_models = frozenset({'deepseek/deepseek-v3.2'})

    def __init__(self, env_file=Path(__file__).resolve().parents[3]/'.env', *, client=None):
        if env_file is not None:
            load_dotenv(env_file, override=False)
        self.model = os.getenv('OPENROUTER_MODEL', 'deepseek/deepseek-v3.2')
        self.base_url = os.getenv('OPENROUTER_BASE_URL', 'https://openrouter.ai/api/v1').rstrip('/')
        self.profile = os.getenv('OPENROUTER_PROFILE', 'tools_json_text')
        self.final_output_mode = {'tools_json_text': 'json_text', 'tools_structured': 'structured'}.get(self.profile)
        self.available = bool(os.getenv('OPENROUTER_API_KEY'))
        self.reasoning_effort = os.getenv('OPENROUTER_REASONING_EFFORT','low')
        if self.reasoning_effort not in {'none','minimal','low','medium','high'}:
            raise PlannerError('planner_configuration','Invalid OPENROUTER_REASONING_EFFORT.',503)
        self.allow_paid = (os.getenv('OPENROUTER_ALLOW_PAID', 'false').lower() == 'true'
                           and self.model in self.approved_paid_models)
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+', self.model)
                or self.model.startswith('openrouter/') or not self.final_output_mode
                or urlparse(self.base_url).scheme != 'https'
                or urlparse(self.base_url).netloc != 'openrouter.ai'
                or urlparse(self.base_url).path != '/api/v1'
                or urlparse(self.base_url).query or urlparse(self.base_url).fragment):
            raise PlannerError('planner_configuration', 'Invalid OpenRouter model, profile or base URL.', 503)
        if not self.allow_paid and not self.model.endswith(':free'):
            raise PlannerError('planner_configuration', 'Paid OpenRouter models require explicit OPENROUTER_ALLOW_PAID=true and an approved model ID.', 503)
        self.client = client or httpx.Client(transport=httpx.HTTPTransport(retries=0), follow_redirects=False)
        self.endpoint = None
        self.last_request_started = None

    @property
    def checkpoint_identity(self):
        return {'provider': self.provider, 'model': self.model, 'profile': self.profile,
                'base_url': self.base_url, 'allow_paid': self.allow_paid, 'reasoning_effort':self.reasoning_effort}

    def restore_content(self, content):
        if 'openrouter_message' in content:
            return NativeMessage(deepcopy(content['openrouter_message']), content['route'],content.get('isolated_final',False))
        return types.Content.model_validate(content)

    def restore_response(self, saved):
        response=OpenRouterResponse(saved['openrouter_response'], saved['route'])
        response.candidates[0].content.isolated_final=saved.get('isolated_final',False)
        return response

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

    def final_contents(self, context, calculations, goal_ref, question, contents, errors=()):
        """Start a fresh final request, retaining original history only in the checkpoint."""
        from .agent_models import PlannerResult
        from .financial_facts import financial_facts
        _,route=self._messages(contents)  # Reject incomplete exchanges; never transplant them.
        self._final_route=route
        goals={g['goal_ref'] for g in context['goals']}
        if not calculations or any(c.get('calculation_ref')!=ref or
            {p['goal_ref'] for p in c['plans']}!=goals or len(c['plans'])!=len(goals) for ref,c in calculations.items()):
            raise PlannerError('planner_invalid_response','Final generation requires complete authorised calculation references.',502)
        keep=('schema_version','as_of','currency','state_version','goal_versions','goals','recorded_goal_schedules',
              'preferences','funding_confirmation','financial_policy_version','pending_expenses','uncertainty')
        compact={k:deepcopy(context[k]) for k in keep if k in context}
        compact['financial_insights']={k:deepcopy(context['financial_insights'][k]) for k in
            ('observation','income','spending','cashflow','balances','uncertainty') if k in context['financial_insights']}
        compact['shocks']=[deepcopy(e) for e in context.get('shocks',[]) if e.get('status') in {'outstanding','anticipated'}
            or e.get('date','')>=context['as_of'] or e.get('event_ref','!not-present!') in question]
        compact['goal_assessments']=[]
        for row in context.get('goal_assessments',[]):
            item={k:deepcopy(v) for k,v in row.items() if k!='creation_assessment'}
            old=row.get('creation_assessment') or {}
            item['creation_assessment']={k:deepcopy(old[k]) for k in ('status','goal_version','schedule','assessed_at') if k in old}
            compact['goal_assessments'].append(item)
        feedback=None
        for content in contents:
            for part in getattr(content,'parts',None) or []:
                if not part.text:continue
                try:value=strict_json(part.text)
                except ValueError:continue
                if isinstance(value,dict) and value.get('task','').startswith('Revise the financial proposal in response to the independent checker'):
                    feedback=value
        precise=[{k:deepcopy(v) for k,v in e.items() if k!='original_claim'} for e in errors]
        payload={'stage':'fresh_final_response','financial_context':compact,'selected_goal_ref':goal_ref,'question':question,
            'authoritative_calculations':deepcopy(calculations),'authoritative_financial_facts':financial_facts(compact,{}),
            'safety_revision_feedback':feedback,'validation_errors':precise,'output_schema':PlannerResult.model_json_schema(),
            'instructions':'Return exactly one bare JSON object matching output_schema, without fences or extra prose. Maintaining a feasible original schedule is valid. One complete recommendation is sufficient. Do not invent alternatives for variety. Omit unsupported or incomplete alternatives. Reference only supplied calculation IDs; changed financial strategies require new Python calculations and cannot be invented in this final stage. Quote monetary values only from financial_context or authoritative_calculations; do not quote numbers found solely in feedback/errors or perform arithmetic in prose. Distinguish historical surplus, capacity, current cash, dated minima and requested deadlines. All schema, narrative and independent safety checks remain mandatory.'}
        return [types.Content(role='user',parts=[types.Part.from_text(text=compact_json(payload))])]

    def _messages(self, contents):
        messages = []
        route = None
        outstanding = set();prior_calculations={}
        for content in contents:
            if isinstance(content, NativeMessage):
                if route and route != content.route:
                    raise ValueError('Mixed routes')
                route = content.route
                if outstanding:
                    raise ValueError('Unanswered tool calls')
                native = deepcopy(content.message)
                if content.isolated_final:
                    if native.get('tool_calls'):raise ValueError('Isolated final response cannot contain tool calls')
                    for key in ('reasoning','reasoning_details','reasoning_content'):native.pop(key,None)
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
                                         'content': compact_json(tool_wire(result.response,prior_calculations))})
                    elif part.text:
                        try:
                            value=strict_json(part.text)
                        except ValueError:
                            texts.append(part.text)
                        else:
                            texts.append(compact_json(correction_feedback(value)) if isinstance(value,dict) else part.text)
                    else:
                        raise ValueError('Unsupported history part')
                if texts:
                    if outstanding:
                        raise ValueError('Unanswered tool calls')
                    messages.append({'role': 'assistant' if content.role == 'model' else 'user', 'content': '\n'.join(texts)})
        if outstanding:
            raise ValueError('Unanswered tool calls')
        corrections=[];safety_feedback_indices=[]
        for index,message in enumerate(messages):
            if message.get('role')!='user':continue
            try:value=strict_json(message.get('content',''))
            except ValueError:continue
            if isinstance(value,dict) and value.get('task')=='Correct the complete proposal using the validation evidence below.':corrections.append(index)
            if isinstance(value,dict) and value.get('task')=='Revise the financial proposal in response to the independent checker, not merely its wording.':safety_feedback_indices.append(index)
        obsolete=set(corrections[:-1])
        if safety_feedback_indices:obsolete.update(i for i in corrections if i<max(safety_feedback_indices))
        for index in sorted(obsolete):
            prior=messages[index-1] if index else {}
            if prior.get('role')=='assistant' and all(not v for k,v in prior.items() if k not in {'role','content'}):
                prior['content']='Earlier rejected draft superseded by the latest assistant proposal; its complete text remains in persistent validation records.'
            messages[index]['content']='Earlier correction feedback is superseded by the latest precise validation findings. Full validation records remain persisted.'
        dedupe_final_facts(messages)
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
            endpoint = self._endpoint(timeout_ms/1000, route or getattr(self,'_final_route',None))
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
            if body['tool_choice']=='none':result.candidates[0].content.isolated_final=True
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
