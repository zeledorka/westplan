"""Local proposal contracts and bounded consistency feedback; not an AI Safety Checker.

Only explicit, recognisable claims are checked. Passing is not independent financial
approval or proof of arbitrary natural-language reasoning.
"""
import json
import re
from datetime import date
from decimal import Decimal
from enum import Enum
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .agent_models import PlannerResult
from .gemini import PlannerError
from .financial_facts import financial_facts
from .narrative import narrative_issues


class Category(str, Enum):
    FORMAT = 'response_format'
    SCHEMA = 'proposal_schema'
    REFERENCE = 'calculation_reference_mismatch'
    DEADLINE = 'deadline_precision_mismatch'
    DATE = 'date_claim_mismatch'
    MONEY = 'monetary_claim_mismatch'
    FEASIBILITY = 'feasibility_claim_mismatch'
    NARRATIVE = 'financial_narrative_mismatch'
    UNAUTHORISED = 'unauthorised_reference'
    ACTION = 'unsupported_action'
    FIELD = 'unsupported_field'
    AUTHORITY = 'protected_calculation_change'
    MISSING = 'missing_authoritative_data'
    INTEGRITY = 'data_integrity'


RECOVERABLE = {Category.FORMAT, Category.SCHEMA, Category.REFERENCE, Category.DEADLINE,
               Category.DATE, Category.MONEY, Category.FEASIBILITY, Category.NARRATIVE}


class Issue(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    error_type: Category
    field: str
    original_claim: str | None = None
    calculation_ref: str | None = None
    authoritative_values: dict = Field(default_factory=dict)
    correction_required: str

    @property
    def recoverable(self):
        return self.error_type in RECOVERABLE


class ProposalValidationError(PlannerError):
    def __init__(self, issues):
        self.issues = tuple(sorted(issues,key=lambda i:i.recoverable)[:16])
        self.recoverable = bool(self.issues) and all(i.recoverable for i in issues)
        categories = ', '.join(sorted({i.error_type.value for i in self.issues}))
        message = f'Gemini proposal failed validation ({categories}). Your goals have not changed.'
        if any(i.error_type == Category.DEADLINE for i in issues):
            message = 'Gemini claimed exact deadline completion inconsistent with Python dates. No draft accepted; your goals have not changed.'
        super().__init__('planner_invalid_response', message)

    def feedback(self):
        return [i.model_dump(mode='json') for i in self.issues]


def date_facts(calculations):
    facts = {}
    for ref, calc in calculations.items():
        rows = []
        for plan in calc['plans']:
            completion = plan['completion_date']
            deadline = plan['deadline']
            relation = 'unknown'
            if completion is not None:
                a, b = date.fromisoformat(completion), date.fromisoformat(deadline)
                relation = 'before' if a < b else 'after' if a > b else 'on'
            rows.append({k: plan[k] for k in ('goal_ref', 'completion_date', 'deadline', 'meets_deadline')} |
                        {'deadline_relation': relation})
        facts[ref] = rows
    return facts


PRECISION = re.compile(r'\b(?:exactly|precisely) (?:on |at |by )?(?:the |your |its |original |specified )*deadline\b|\bdeadline (?:exactly|precisely)\b|\b(?:on|at) (?:the |your |its |original |specified )*deadline\b', re.I)


def deadline_issue(field, text, refs, calculations):
    if PRECISION.search(text):
        for ref in refs:
            facts = date_facts({ref: calculations[ref]})[ref]
            if any(p['deadline_relation'] != 'on' for p in facts):
                return Issue(error_type=Category.DEADLINE, field=field, original_claim=text,
                    calculation_ref=ref, authoritative_values={'plans': facts},
                    correction_required='Use the Python completion date and deadline_relation. Meeting a deadline does not imply completion on that day. Preserve the calculated strategy.')
    return None


def text_fields(result):
    for name in ('objective', 'answer'):
        yield name, getattr(result, name), None
    for name in ('assumptions', 'missing_information'):
        for index, text in enumerate(getattr(result, name)):
            yield f'{name}[{index}]', text, None
    for index, strategy in enumerate(result.strategies):
        for name, value in strategy.model_dump().items():
            if name == 'calculation_ref':
                continue
            if isinstance(value, str):
                yield f'strategies[{index}].{name}', value, strategy.calculation_ref
            else:
                for n, text in enumerate(value):
                    yield f'strategies[{index}].{name}[{n}]', text, strategy.calculation_ref


NUMBER = r'[-−]?\d+(?:,\d{3})*(?:\.\d+)?'
MONEY_VALUE = rf'(?:AUD\s*|A?\$\s*)?(?P<amount>{NUMBER})(?:\s*AUD)?'
CONTRIBUTION = re.compile(r'\b(?:contribut(?:e|ing|ion[s]?)|sav(?:e|ing[s]?)|allocat(?:e|ing|ion[s]?))\s+(?:of\s+|to\s+|at\s+|is\s+|are\s+|a\s+)?' + MONEY_VALUE + r'(?:\s*(?:(?:per|each|a)\s+(?P<period>month|fortnight)|(?P<frequency>monthly|fortnightly)))?(?!\d|\.\d)', re.I)
MONTHLY_CONTRIBUTION = re.compile(r'\b(?P<frequency>monthly|fortnightly)\s+(?:contribution[s]?|saving[s]?|allocation[s]?)\s*(?:of|to|is|are|:)?\s*' + MONEY_VALUE + r'(?!\d|\.\d)', re.I)
CURRENCY = re.compile(r'(?P<leading>[-−]?)\s*(?:AUD\s*|A?\$\s*)(?P<prefix>' + NUMBER + r')(?!\d|\.\d)|(?P<suffix>' + NUMBER + r')\s*AUD\b', re.I)
SURPLUS = re.compile(r'\b(?:(?P<historical>historical(?:\s+average)?|average)\s+)?(?:remaining\s+|residual\s+)?monthly\s+(?:cashflow\s+)?surplus(?:\s+after\s+(?:the\s+)?contribution)?\s*(?:of|is|:)?\s*' + MONEY_VALUE + r'(?!\d|\.\d)',re.I)
ISO_DATE = re.compile(r'\b\d{4}-\d{2}-\d{2}\b')
COMPLETION = re.compile(r'\b(?:completion(?: date)?\s*(?:of|is|:)?|complete[sd]?\s+(?:on|at)|finish(?:es)?\s+on|reach(?:es)? (?:the )?(?:goal|target)\s+on)\s*(\d{4}-\d{2}-\d{2})', re.I)


NON_MONETARY = {'complete_months','months','observations','period_days','transactions','version',
                'dismiss','not_useful','reject_adjustment','useful','variability_cv','ewma_w',
                'periods','pause_months','priority','interest_assumed','count','confidence','score'}


def numbers_in(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield Decimal(str(value))
    elif isinstance(value, dict):
        for key,item in value.items():
            if key not in NON_MONETARY and not key.endswith(('_count','_ratio','_score')):
                yield from numbers_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from numbers_in(item)


def dates_in(value):
    if isinstance(value, str):
        yield from ISO_DATE.findall(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from dates_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from dates_in(item)


def consistency_issues(result, calculations, context):
    issues = []
    all_refs = [s.calculation_ref for s in result.strategies]
    facts = date_facts(calculations)
    financial = financial_facts(context,calculations)
    for field, text, ref in text_fields(result):
        refs = [ref] if ref else all_refs
        if not refs:
            continue
        issues.extend(Issue(error_type=Category(claim.pop('category')),**claim) for claim in narrative_issues(field,text,ref,all_refs,financial))
        plans = [p for r in refs for p in calculations[r]['plans']]
        authority = {'plans':[{k:p[k] for k in ('goal_ref','contribution','frequency','required_per_period','monthly_equivalent','target','allocated','completion_date','deadline','meets_deadline')} for p in plans], 'date_facts':{r:facts[r] for r in refs}, 'cashflow':{r:calculations[r]['one_month_comparison'] for r in refs}}
        def issue(category, explanation):
            issues.append(Issue(error_type=category, field=field, original_claim=text,
                calculation_ref=ref, authoritative_values=authority, correction_required=explanation))
        if ('proposed_actions' in field and re.search(r'\b(?:activate|approve|execute)\b',text,re.I) and not re.search(r'\b(?:not|never|cannot)\b',text,re.I)) or re.search(r'\b(?:I|we) have (?:activated|approved|transferred|paid)\b',text,re.I):
            issue(Category.ACTION,'This proposal cannot activate, approve or execute customer actions or claim that funds were moved.')
        precision = deadline_issue(field, text, refs, calculations)
        if precision:
            issues.append(precision)
        surplus_claims=list(SURPLUS.finditer(text))
        for pattern in (CONTRIBUTION, MONTHLY_CONTRIBUTION):
            for match in pattern.finditer(text):
                # "surplus after contribution is AUD 300" states a residual, not a
                # contribution of 300. Its same amount is checked against Python below.
                if any(match.span('amount')==surplus.span('amount') for surplus in surplus_claims):
                    continue
                value = Decimal(match['amount'].replace(',', '').replace('−','-'))
                groups = match.groupdict()
                frequency = groups.get('frequency') or {'month': 'monthly', 'fortnight': 'fortnightly'}.get(groups.get('period'))
                prefix=text[max(0,match.start()-30):match.start()].lower()
                key='required_per_period' if re.search(r'\brequired(?:\s+(?:monthly|fortnightly))?\s*$',prefix) else 'monthly_equivalent' if re.search(r'\bmonthly equivalent\s*$',prefix) else 'contribution'
                expected = [Decimal(str(p[key])) for p in plans if p[key] is not None and (frequency is None or p['frequency'] == frequency.lower())]
                if value not in expected:
                    issue(Category.MONEY, 'The contribution amount/frequency must match this strategy\'s Python plans. Correct the wording; a different financial schedule requires a separately calculated strategy. Do not invent or recalculate amounts in prose.')
        for match in surplus_claims:
            value=Decimal(match['amount'].replace(',','').replace('−','-'))
            expected=[context['financial_insights']['cashflow']['average_surplus']] if match['historical'] else [calculations[r]['one_month_comparison']['monthly_surplus_after_contribution'] for r in refs if calculations[r]['one_month_comparison'] is not None]
            if value not in {Decimal(str(v)) for v in expected}:
                authority['historical_average_monthly_surplus']=context['financial_insights']['cashflow']['average_surplus']
                authority['capacity_estimates']={r:{k:calculations[r][k] for k in ('comfortable_capacity','stretch_capacity')} for r in refs}
                issue(Category.MONEY,'Distinguish historical monthly surplus (financial_insights.cashflow.average_surplus, before the proposed contribution), capacity estimates and monthly surplus after contribution (the linked Python one_month_comparison.monthly_surplus_after_contribution). Label a historical amount explicitly as historical monthly surplus; quote each strategy\'s own Python residual for a remaining surplus. A cash balance is not a monthly surplus. Do not invent or recompute amounts in prose.')
        allowed_money = set(numbers_in(context)) | {v for r in refs for v in numbers_in(calculations[r])}
        for match in CURRENCY.finditer(text):
            value=Decimal((match['prefix'] or match['suffix']).replace(',', '').replace('−','-'))
            if match['leading'] in {'-','−'}:value=-abs(value)
            if value not in allowed_money:
                issue(Category.MONEY, 'This monetary amount is absent from the authorised context and linked Python results. Use an authoritative value or remove the unsupported claim; do not invent assumptions.')
        allowed_dates = set(dates_in(context)) | {d for r in refs for d in dates_in(calculations[r])}
        if any(d not in allowed_dates for d in ISO_DATE.findall(text)):
            issue(Category.DATE, 'Use only dates supported by the authorised context and linked Python calculation. Different target dates require a calculated strategy.')
        for match in COMPLETION.finditer(text):
            if match[1] not in {p['completion_date'] for p in plans}:
                issue(Category.DATE, 'The explicit completion date must equal Python completion_date, not the requested deadline.')
        # Narrow affirmative assertions; do not substitute Python for Gemini's overall assessment.
        # Schedule claims are checked per identifiable clause/reference above, rather
        # than applying one strategy's missed deadline to a different strategy's prose.
    unique={}
    for issue in issues:unique.setdefault((issue.error_type,issue.field,issue.calculation_ref),issue)
    return list(unique.values())


def _path(loc):
    return ''.join(f'[{part}]' if isinstance(part, int) else ('.' if i else '') + str(part) for i, part in enumerate(loc)) or '$'


def validate_proposal(text, calculations, context):
    """Every call parses and revalidates the complete proposal against the unchanged ledger."""
    if not calculations:
        raise ProposalValidationError([Issue(error_type=Category.MISSING, field='$', correction_required='Authoritative Python calculations are missing; no draft can be accepted.')])
    if len(text) > 64000:
        raise ProposalValidationError([Issue(error_type=Category.INTEGRITY, field='$', correction_required='Proposal exceeds the bounded output size.')])
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProposalValidationError([Issue(error_type=Category.INTEGRITY, field='$', correction_required='Duplicate JSON fields are unsupported.')])
            result[key] = value
        return result
    def invalid_constant(_):
        raise ValueError('Non-finite JSON literal')
    try:
        raw = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (json.JSONDecodeError, ValueError):
        raise ProposalValidationError([Issue(error_type=Category.FORMAT, field='$', original_claim=text,
            correction_required='Return the complete proposal as valid JSON only, without markdown, non-finite values or surrounding prose. Do not fill unknown financial information.')]) from None
    if isinstance(raw, dict):
        refs = [s.get('calculation_ref') for s in raw.get('strategies', []) if isinstance(s, dict)] if isinstance(raw.get('strategies'), list) else []
        refs += [raw.get('recommended_ref')] if raw.get('recommended_ref') is not None else []
        shocks = raw.get('shock_refs', [])
        if any(isinstance(r, str) and r not in calculations for r in refs) or isinstance(shocks, list) and any(isinstance(r, str) and r not in {s['event_ref'] for s in context['shocks']} for r in shocks):
            raise ProposalValidationError([Issue(error_type=Category.UNAUTHORISED, field='references', correction_required='Unknown calculation or event reference. No authoritative reference can be inferred; reject this proposal.')])
    try:
        result = PlannerResult.model_validate(raw)
    except ValidationError as exc:
        issues = []
        for error in exc.errors(include_context=False, include_url=False):
            path = _path(error['loc']); category = Category.SCHEMA
            correction = f'Proposal contract failed ({error["type"]}). Follow the existing proposal schema; do not infer missing financial facts.'
            if error['type'] == 'extra_forbidden':
                key = str(error['loc'][-1])
                category = Category.UNAUTHORISED if key in {'goal_id', 'goal_ref', 'customer_id', 'customer_ref'} else Category.AUTHORITY if key in {'calculations', 'contribution', 'completion_date', 'deadline', 'financial_version'} else Category.ACTION if key in {'activate', 'transfer', 'execute', 'approve', 'actions'} else Category.FIELD
                # Observed encoding mistake, not an accepted field or permission to act.
                # Keep all other extra fields (including nested/action-valued variants) fail-closed.
                if error['loc'] == ('change_requested',) and type(error.get('input')) is bool:
                    category = Category.SCHEMA
                    correction = ('Remove the unsupported top-level boolean field change_requested. '
                                  'Do not rename it or copy its value into change_recommended. '
                                  'change_recommended describes your recommendation, not whether the user requested a change; '
                                  'preserve its independently justified schema-valid value and existing calculated strategy. '
                                  'Return the complete proposal with only schema-supported fields. '
                                  'This correction grants no approval, activation or execution authority.')
            elif error['type'] == 'string_type' and 'proposed_actions' in error['loc']:
                category = Category.ACTION
            elif error['type'] == 'value_error' and 'Recommendation' in error['msg']:
                category = Category.REFERENCE
            issues.append(Issue(error_type=category, field=path, original_claim=json.dumps(error.get('input'),ensure_ascii=True,default=str)[:2400] if 'input' in error else None, correction_required=correction))
        raise ProposalValidationError(issues) from None
    authorised = {g['goal_ref'] for g in context['goals']}
    for ref, calc in calculations.items():
        refs = [p['goal_ref'] for p in calc['plans']]
        if len(refs) != len(set(refs)) or set(refs) != authorised:
            raise ProposalValidationError([Issue(error_type=Category.INTEGRITY, field='calculations', correction_required='Python calculation ledger does not match the authorised goal scope.')])
    for field, value, ref in text_fields(result):
        if any(g not in authorised for g in re.findall(r'\bgoal-\d+\b', value)):
            raise ProposalValidationError([Issue(error_type=Category.UNAUTHORISED, field=field, correction_required='Narrative contains a foreign goal reference.')])
    if result.assessment != 'needs_clarification' and not result.strategies:
        raise ProposalValidationError([Issue(error_type=Category.SCHEMA, field='strategies', correction_required='A non-clarification proposal requires at least one strategy backed by successful Python calculations.')])
    issues = consistency_issues(result, calculations, context)
    if issues:
        raise ProposalValidationError(issues)
    return result


def correction_limit():
    """Small configurable ceiling; shared agent call/time budgets remain authoritative."""
    import os
    try:
        value = int(os.getenv('GEMINI_MAX_CORRECTIONS', '4'))
    except (ValueError, OverflowError):
        raise PlannerError('planner_configuration', 'Invalid GEMINI_MAX_CORRECTIONS; use an integer from 0 to 4.', 503) from None
    if not 0 <= value <= 4:
        raise PlannerError('planner_configuration', 'Invalid GEMINI_MAX_CORRECTIONS; use an integer from 0 to 4.', 503)
    return value
