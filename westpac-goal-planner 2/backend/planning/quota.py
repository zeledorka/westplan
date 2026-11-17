"""Classify only evidenced Google quota failures; retain no raw error messages/subjects."""
import math
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def duration(value):
    try:
        if isinstance(value, dict):
            seconds=float(value.get('seconds',0))+float(value.get('nanos',0))/1e9
        else:
            seconds=float(str(value).removesuffix('s'))
        return seconds if math.isfinite(seconds) and 0<=seconds else None
    except (TypeError,ValueError,OverflowError):
        return None


def quota_guidance(details, header_delay=None, *, header_present=False, now=None):
    """Return safe diagnostics and retry policy, without inferring the historical quota."""
    now=datetime.now(timezone.utc) if now is None else now
    body=details.get('error',details) if isinstance(details,dict) else {}
    if not isinstance(body,dict):body={}
    safe={'classification':'unknown','retry_after_present':header_present,
          'retry_after_valid':header_delay is not None,'quota_violations':[],'error_info':[]}
    if header_delay is not None:safe['retry_after_header_seconds']=header_delay
    status=body.get('status')
    if status in {'RESOURCE_EXHAUSTED','UNAVAILABLE','PERMISSION_DENIED'}:safe['status']=status
    hints=[header_delay] if header_delay is not None else []
    identifiers=[];zero=False
    def identifier(value):
        # Identifiers only: never retain free-form messages, project subjects or arbitrary metadata.
        return value if isinstance(value,str) and len(value)<=200 and re.fullmatch(r'[A-Za-z0-9_.:/-]+',value) else None
    records=body.get('details')
    records=records if isinstance(records,list) else []
    for item in records[:16]:
        if not isinstance(item,dict):continue
        kind=str(item.get('@type','')).rsplit('/',1)[-1]
        if kind=='google.rpc.RetryInfo':
            delay=duration(item.get('retryDelay'))
            if delay is not None:
                hints.append(delay);safe['retry_info_seconds']=delay
        elif kind=='google.rpc.QuotaFailure':
            violations=item.get('violations')
            violations=violations if isinstance(violations,list) else []
            for violation in violations[:8]:
                if not isinstance(violation,dict):continue
                entry={}
                for key in ('quotaMetric','quotaId'):
                    value=identifier(violation.get(key))
                    if value:entry[key]=value;identifiers.append(value)
                value=violation.get('quotaValue')
                if re.fullmatch(r'\d+(?:\.\d+)?',str(value)):
                    entry['quotaValue']=str(value)[:32];zero=zero or float(value)==0
                if entry:safe['quota_violations'].append(entry)
        elif kind=='google.rpc.ErrorInfo':
            entry={key:identifier(item.get(key)) for key in ('reason','domain') if identifier(item.get(key))}
            if entry.get('reason'):identifiers.append(entry['reason'])
            metadata=item.get('metadata') or {}
            if not isinstance(metadata,dict):metadata={}
            for key in ('quota_metric','quota_limit','quota_limit_value'):
                value=identifier(metadata.get(key))
                if value:
                    entry[key]=value;identifiers.append(value)
                    if key=='quota_limit_value' and value=='0':zero=True
            safe['error_info'].append(entry)
    # Older Google responses sometimes have only an explicit zero limit in their message.
    zero=zero or bool(re.search(r'(?:limit|quotaValue)[\"\':=\s]+0(?:\D|$)',str(body.get('message',''))))
    evidence=' '.join(identifiers).lower()
    daily=bool(re.search(r'per[_-]?day|daily',evidence))
    daily_requests=any('request' in str(entry).lower() and re.search(r'per[_-]?day|daily',str(entry).lower())
                       for entry in safe['quota_violations']+safe['error_info'])
    minute=bool(re.search(r'per[_-]?minute',evidence))
    second=bool(re.search(r'per[_-]?second',evidence))
    delay=max(hints,default=0)
    retryable=False
    if zero:
        safe.update(classification='zero_quota',resume_condition='Quota availability must change; do not enable billing automatically.')
    elif daily:
        safe.update(classification='daily_quota',resume_condition='Wait for quota reset; confirm availability before resuming.')
        # Google documents midnight Pacific reset for requests per day only.
        if daily_requests:
            local=now.astimezone(ZoneInfo('America/Los_Angeles'))
            reset=datetime.combine(local.date()+timedelta(days=1),datetime.min.time(),tzinfo=local.tzinfo)
            delay=max(delay,(reset-now).total_seconds())
            safe['expected_reset_at']=reset.astimezone(timezone.utc).isoformat()
    elif any(entry.get('reason') in {'BILLING_DISABLED','API_KEY_SERVICE_BLOCKED','SERVICE_DISABLED','ACCESS_DENIED'} for entry in safe['error_info']):
        safe.update(classification='project_restriction',resume_condition='Resolve the reported project restriction before resuming; do not enable billing automatically.')
    elif minute or second:
        safe['classification']='temporary_rate_limit';retryable=True
        delay=max(delay,60 if minute else 1)
    elif hints:
        safe['classification']='retry_guidance';retryable=True
    else:
        safe['resume_condition']='Quota type unknown; inspect project quota before manually resuming.'
    safe['retry_after_seconds']=delay or None
    return safe,retryable,delay or None
