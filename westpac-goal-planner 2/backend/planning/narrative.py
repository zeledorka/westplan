"""Conservative explicit claim checks, not general prose understanding or financial advice."""
import re
from datetime import datetime
from decimal import Decimal

NUM=r'[-−]?\d+(?:,\d{3})*(?:\.\d+)?'
AMOUNT=rf'(?P<amount>[-−]?\s*(?:AUD\s*|A?\$\s*)?{NUM})(?!\d|\.\d)'
METRIC=r'(?:(?:current|today.s|projected|immediate|one.month|remaining|available|post.contribution)\s+)*(?:(?:transaction(?:\s+account)?|cash|account)(?:\s+(?:balance|residual|available|shortfall))?|balance|liquidity|(?:monthly\s+)(?:cashflow\s+)?(?:surplus|residual|deficit))'
SIGN=r'positive|negative|zero|non-negative|nonnegative|non-positive|nonpositive|no'
SIGN_BEFORE=re.compile(rf'\b(?P<sign>{SIGN})\s+(?P<metric>{METRIC})\b',re.I)
SIGN_AFTER=re.compile(rf'\b(?P<metric>{METRIC})\s+(?:is|remains|will (?:be|remain)|would be)\s+(?P<sign>{SIGN})\b',re.I)
VALUE_AFTER=re.compile(rf'\b(?P<metric>{METRIC})(?:\s+after\s+(?:the\s+)?(?:(?:proposed|known|confirmed)\s+)?(?:savings|contributions?|obligations))?\s*(?:is|of|:|equals|will be)\s*{AMOUNT}',re.I)
DATE=r'(?:\d{4}-\d{2}-\d{2}|\d{1,2}\s+[A-Za-z]+\s+\d{4}|[A-Za-z]+\s+\d{1,2},?\s+\d{4})'
NAMED_DATE=re.compile(rf'\b(?P<metric>completion(?: date)?|deadline)\s*(?:is|of|:|on|by)?\s*(?P<date>{DATE})\b',re.I)
DATED_RELATION=re.compile(rf'(?P<completion>{DATE}),?\s+(?:is\s+)?(?P<relation>before|after|on)\s+(?:the |your |requested |original )*deadline\s+(?:of\s+|is\s+)?(?P<deadline>{DATE})\b',re.I)
RELATION=re.compile(r'\b(?:complete[sd]?|finish(?:es)?|reach(?:es)? (?:the )?(?:goal|target))\s+(?P<relation>before|on|after|by)\s+(?:the |your |requested |original )*deadline\b',re.I)
MEETS=re.compile(r'\bmeets? (?:the |its |your |original |requested |specified )*deadline\b',re.I)


def parsed_date(value):
    for fmt in ('%Y-%m-%d','%d %B %Y','%d %b %Y','%B %d %Y','%b %d %Y'):
        try:return datetime.strptime(value.replace(',',''),fmt).date().isoformat()
        except ValueError:pass
    return None


def clauses(text):
    # Keep decimals and named dates intact. Contrastive clauses have independent polarity.
    return [s.strip() for s in re.split(r'(?<=[.!?;])\s+(?=[A-Z])|;\s*|,?\s+\bbut\b\s+',text) if s.strip()]


def metric_name(phrase, statement):
    phrase=phrase.lower();statement=statement.lower()
    if re.search(r'one.month|immediate',phrase) and not ('monthly' in phrase and re.search(r'surplus|residual|deficit',phrase)):return 'legacy_scalar_cash_comparison'
    if 'monthly' in phrase and re.search(r'surplus|residual|deficit',phrase):
        return 'historical_monthly_surplus' if re.search(r'\b(?:historical|average)\b',statement[:statement.find(phrase)+len(phrase)]) else 'monthly_surplus_after_contribution'
    if re.search(r'\b(?:current|today)\b',phrase):return 'current_cash_balance'
    # A transaction-account balance is a current stock unless explicitly projected.
    if 'transaction' in phrase and not re.search(r'projected|post.contribution',phrase) and not re.search(r'after.*(?:contribution|savings|obligations)',statement):return 'current_cash_balance'
    if re.search(r'\b(?:before (?:the |proposed )*(?:contribution|savings)|after (?:known |confirmed )*obligations)\b',statement) and not re.search(r'after.*(?:contribution|savings)',statement):return 'cash_after_known_obligations'
    return 'cash_after_contribution'


def sign_ok(sign, value, metric):
    if 'shortfall' in metric.lower() or 'deficit' in metric.lower():
        return value>=0 if sign=='no' else value<0 if sign=='positive' else value==0 if sign=='zero' else None
    return {'positive':value>0,'negative':value<0,'zero':value==0,'nonnegative':value>=0,
            'non-negative':value>=0,'nonpositive':value<=0,'non-positive':value<=0}.get(sign)


def narrative_issues(field, text, strategy_ref, all_refs, facts):
    found=[]
    context={f['metric']:f for f in facts['context']}
    def evidence(metric, refs):
        if metric in context and metric not in {'cash_after_known_obligations'}:return [context[metric]]
        return [f for ref in refs for f in facts['calculations'][ref]['facts'] if f['metric']==metric]
    def emit(statement, refs, authoritative, instruction, category='financial_narrative_mismatch'):
        if authoritative:found.append({'category':category,'field':field,'original_claim':statement,
            'calculation_ref':refs[0] if len(refs)==1 else None,
            'authoritative_values':{'facts':authoritative},'correction_required':instruction+' Preserve the strategy and existing Python calculations. No new financial parameters or invented funding; return a complete proposal for full revalidation.'})
    for statement in clauses(text):
        # Future hypothetical periods cannot be equated to this one-month comparison.
        if re.search(r'\b(?:could|might|may(?!\s+\d)|if|after (?:the )?(?:next )?payday|next year)\b',statement,re.I):continue
        explicit=[r for r in re.findall(r'\bcalculation-\d+\b',statement.lower()) if r in all_refs]
        refs=[strategy_ref] if strategy_ref else list(dict.fromkeys(explicit)) or all_refs
        # If a global sentence describes different strategies, require explicit scope/consensus.
        for pattern in (SIGN_BEFORE,SIGN_AFTER,VALUE_AFTER):
            for match in pattern.finditer(statement):
                phrase=match['metric'];metric=metric_name(phrase,statement)
                # Existing surplus amount checks provide their established detailed
                # feedback; this layer adds cash-stock amounts and flow sign checks.
                if pattern is VALUE_AFTER and 'surplus' in phrase.lower():continue
                candidates=evidence(metric,refs)
                if not candidates:continue
                local=statement[max(0,match.start()-32):match.start()]
                if re.search(r'\b(?:not|never|without)\s+(?:\w+\s+){0,2}$',local,re.I):continue
                values=[Decimal(str(f['value'])) for f in candidates]
                if pattern is VALUE_AFTER:
                    claim=Decimal(re.sub(r'AUD|A?\$|\s|,','',match['amount'],flags=re.I).replace('−','-'))
                    ok=claim in values
                else:ok=not all(sign_ok(match['sign'].lower(),v,phrase) is False for v in values)
                if ok is False:
                    emit(statement,refs,candidates,'The identified '+metric+' claim contradicts Python. A monthly flow is not a cash balance; distinguish current, after-obligation and after-contribution amounts and acknowledge any cash shortfall.')
        # Explicit affordability/no-shortfall assertions only; do not judge which strategy is best.
        if re.search(r'\b(?:no (?:projected |immediate |one.month )?(?:cash(?:flow)? shortfall|liquidity deficit)|affordable (?:from|with) (?:the |current |available )*cash|cash covers (?:the )?contribution)\b',statement,re.I):
            legacy=bool(re.search(r'one.month|immediate|from current cash|with current cash|current cash covers',statement,re.I))
            candidates=evidence('legacy_scalar_cash_comparison' if legacy else 'cash_after_contribution',refs)
            if candidates and all(f['value']<0 for f in candidates):
                emit(statement,refs,candidates,'Python calculates a negative cash balance after contribution, so an explicit no-shortfall/cash-funded affordability claim is unsupported.')
        goals=[f for ref in refs for rows in facts['calculations'][ref]['goals'].values() for f in rows]
        explicit_goals=re.findall(r'\bgoal-\d+\b',statement)
        if explicit_goals:goals=[f for f in goals if any('.'+g+'.' in f['source'] for g in explicit_goals)]
        for match in NAMED_DATE.finditer(statement):
            metric='goal_deadline' if match['metric'].lower()=='deadline' else 'completion_date'
            candidates=[f for f in goals if f['metric']==metric]
            value=parsed_date(match['date'])
            if value and candidates and value not in {f['value'] for f in candidates}:
                emit(statement,refs,candidates,'The explicit '+metric+' is incorrect. Completion date and requested deadline are distinct; quote the exact Python dates and their before/on/after relationship.')
        for match in RELATION.finditer(statement):
            candidates=[f for f in goals if f['metric']=='deadline_relation']
            allowed={'before':{'before'},'on':{'on'},'after':{'after'},'by':{'before','on'}}[match['relation'].lower()]
            if candidates and all(f['value'] not in allowed for f in candidates):
                emit(statement,refs,candidates,'The claimed completion/deadline relationship contradicts Python.')
        for match in DATED_RELATION.finditer(statement):
            completion=parsed_date(match['completion']);deadline=parsed_date(match['deadline'])
            # Enforce only dates actually belonging to a linked Python plan, not an
            # unrelated historical/future event mentioned in the same sentence.
            relevant=[]
            for ref in refs:
                for rows in facts['calculations'][ref]['goals'].values():
                    values={f['metric']:f['value'] for f in rows}
                    if values.get('completion_date')==completion and values.get('goal_deadline')==deadline:
                        relevant.extend(f for f in rows if f['metric'] in {'completion_date','goal_deadline','deadline_relation'})
            if relevant and any(f['metric']=='deadline_relation' and f['value']!=match['relation'].lower() for f in relevant):
                emit(statement,refs,relevant,'The dates are quoted correctly but their before/on/after relationship contradicts Python. Preserve both dates and correct the relationship.')
        candidates=[f for f in goals if f['metric']=='meets_deadline']
        if MEETS.search(statement) and not re.search(r'\b(?:not|cannot|won.t|doesn.t|fails? to)\b',statement,re.I) and candidates:
            if all(not f['value'] for f in candidates) or re.search(r'\b(?:both|all)\b',statement,re.I) and any(not f['value'] for f in candidates):
                emit(statement,refs,candidates,'The claimed schedule feasibility contradicts Python; say which calculated option meets or misses the deadline.','feasibility_claim_mismatch')
        for capacity in ('comfortable','stretch'):
            match=re.search(r'\bwithin (?:the |your |its )*'+capacity+r' (?:savings )?capacity\b',statement,re.I)
            candidates=evidence('within_'+capacity+'_capacity',refs)
            if match and candidates and not re.search(r'\b(?:not|outside)\b',statement[:match.start()],re.I):
                if all(not f['value'] for f in candidates):
                    emit(statement,refs,candidates,'The claimed '+capacity+' capacity comparison contradicts Python; capacity and cash liquidity are different checks.','feasibility_claim_mismatch')
        # Status checks require an explicit event ref, or one uniquely identifiable expense.
        events=facts['obligations'];named=re.findall(r'\bevent-[a-z0-9]+\b',statement)
        events=[e for e in events if e['event_ref'] in named] if named else events if len(events)==1 and re.search(r'\b(?:bill|expense|obligation)\b',statement,re.I) else []
        for event in events:
            data={f['metric']:f for f in event['facts']}
            paid=re.search(r'\b(?:(?:already|is|was) paid|has (?:already )?been paid|already deducted (?:from|in) (?:the |current )*balance)\b',statement,re.I)
            unpaid=re.search(r'\b(?:still (?:unpaid|payable|outstanding)|remains? (?:unpaid|payable|outstanding)|still (?:needs?|has) to (?:be paid|pay))\b',statement,re.I)
            if paid and re.search(r'\b(?:not|never)\s+(?:\w+\s+){0,2}$',statement[:paid.start()],re.I):paid=None
            if unpaid and re.search(r'\b(?:not|never)\s+(?:\w+\s+){0,2}$',statement[:unpaid.start()],re.I):unpaid=None
            double=re.search(r'\b(?:subtract|deduct|pay)\b.*\bagain\b',statement,re.I) and not re.search(r'\b(?:not|never|without|avoid)\b',statement,re.I)
            omitted=re.search(r'\b(?:not|isn.t) (?:already )?included in (?:the )?(?:cash )?projection\b',statement,re.I)
            if paid and event['status']=='outstanding' or unpaid and event['status']=='posted' or double and (event['status']=='posted' or data.get('included_in_projection',{}).get('value') is True) or omitted and data.get('included_in_projection',{}).get('value') is True:
                emit(statement,refs,event['facts'],'The expense status/debit claim contradicts source evidence: '+event['status']+'. Posted expenses already affect balances; outstanding bills remain payable and must enter the projection exactly once.')
    return found
