"""Official Google SDK adapter. No SDK exceptions, requests or credentials are logged."""
import os
import math
import time
from email.utils import parsedate_to_datetime
from dataclasses import dataclass
from pathlib import Path
import httpx
try:
    import httpx2
except ImportError:
    httpx2 = httpx
from dotenv import load_dotenv
from google import genai
from google.genai import types, errors
from .quota import quota_guidance

class PlannerError(Exception):
    def __init__(self, code, message, status=502, *, upstream_status=None, retry_after_seconds=None, retryable=None, resume_allowed=None, provider_details=None):
        self.code,self.message,self.status=code,message,status
        self.upstream_status=upstream_status
        self.provider_details=provider_details
        self.retry_after_seconds=retry_after_seconds
        self.retryable=upstream_status in {503,504} if retryable is None else retryable
        self.resume_allowed=self.retryable if resume_allowed is None else resume_allowed
        super().__init__(message)

@dataclass(frozen=True)
class GenerationRetryPolicy:
    max_retries: int = 4
    max_attempts: int = 20  # Shared across all stages within one explicitly requested execution.
    base_seconds: float = 5.0
    max_seconds: float = 40.0

    def __post_init__(self):
        valid=(type(self.max_retries) is int and 0<=self.max_retries<=6
               and type(self.max_attempts) is int and 1<=self.max_attempts<=32
               and all(isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
                       for v in (self.base_seconds,self.max_seconds))
               and .1<=self.base_seconds<=30 and self.base_seconds<=self.max_seconds<=120)
        if not valid:
            raise PlannerError('planner_configuration','Invalid backend Gemini retry limits.',503)

    @classmethod
    def from_environment(cls):
        try:
            return cls(max_retries=int(os.getenv('GEMINI_MAX_RETRIES','4')),
                       max_attempts=int(os.getenv('GEMINI_MAX_GENERATION_ATTEMPTS','20')),
                       base_seconds=float(os.getenv('GEMINI_RETRY_BASE_SECONDS','5')),
                       max_seconds=float(os.getenv('GEMINI_RETRY_MAX_SECONDS','40')))
        except (ValueError,OverflowError):
            raise PlannerError('planner_configuration','Invalid backend Gemini retry limits.',503) from None


def bounded_setting(name, default, low, high):
    try:
        value=int(os.getenv(name,str(default)))
        if not low<=value<=high: raise ValueError()
        return value
    except (ValueError,OverflowError):
        raise PlannerError('planner_configuration',f'Invalid backend {name}.',503) from None


def execution_deadline():
    return bounded_setting('GEMINI_EXECUTION_DEADLINE_SECONDS',300,30,900)


def retry_after(response, now=None):
    headers=getattr(response,'headers',{}) or {}
    value=headers.get('Retry-After') or headers.get('retry-after')
    if value is None:return None
    try:
        seconds=float(value)
    except (ValueError,TypeError):
        try:
            stamp=parsedate_to_datetime(value)
            if stamp.tzinfo is None:return None
            seconds=max(0,stamp.timestamp()-(time.time() if now is None else now))
        except (ValueError,TypeError,OverflowError):return None
    return seconds if math.isfinite(seconds) and 0<=seconds else None



class GeminiTransport:
    provider = 'google_genai'
    def __init__(self, env_file=Path(__file__).resolve().parents[3]/'.env'):
        if env_file is not None:
            load_dotenv(env_file,override=False)
        self.model=os.environ.get('GEMINI_MODEL','gemini-3.8-flash')
        self.available=bool(os.environ.get('GEMINI_API_KEY'))
        self.client=None

    def generate(self, contents, config, timeout_ms):
        if not self.available:
            raise PlannerError('planner_unconfigured','Gemini is unavailable: configure GEMINI_API_KEY on the backend.',503)
        try:
            if self.client is None:
                self.client=genai.Client(api_key=os.environ['GEMINI_API_KEY'],http_options=types.HttpOptions(
                    timeout=30000,retry_options=types.HttpRetryOptions(attempts=1)))
            config.http_options=types.HttpOptions(timeout=timeout_ms,retry_options=types.HttpRetryOptions(attempts=1))
            return self.client.models.generate_content(model=self.model,contents=contents,config=config)
        except (httpx.TimeoutException,httpx2.TimeoutException,TimeoutError):
            raise PlannerError('planner_timeout','Gemini took too long. Your goals have not changed. You can resume explicitly.',504,resume_allowed=True) from None
        except errors.APIError as exc:
            code=getattr(exc,'code',0)
            guidance=retry_after(getattr(exc,'response',None))
            if code in {408,504}:
                raise PlannerError('planner_timeout','Gemini timed out. Your goals have not changed.',504,upstream_status=code,retry_after_seconds=guidance) from None
            if code==429:
                headers=getattr(getattr(exc,'response',None),'headers',{}) or {}
                diagnostic,can_retry,delay=quota_guidance(exc.details,guidance,
                    header_present='retry-after' in {str(k).lower() for k in headers})
                raise PlannerError('planner_rate_limited',
                    'Gemini quota or rate limit reached. Completed work is checkpointed; resume when quota is available.',
                    429,upstream_status=429,retry_after_seconds=delay,retryable=can_retry,
                    resume_allowed=True,provider_details=diagnostic) from None
            if code in {401,403,404}:
                raise PlannerError('planner_configuration','Gemini rejected the configured key or model. Check the backend configuration.',503,upstream_status=code,retryable=False) from None
            raise PlannerError('planner_provider_error','Gemini is temporarily unavailable. No recommendation was substituted.',502,upstream_status=code,retry_after_seconds=guidance) from None
        except Exception:
            raise PlannerError('planner_provider_error','Gemini could not complete the request. Your goals have not changed.',502) from None
