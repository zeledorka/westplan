"""Bounded read-only scenarios over the existing v1.2 context and arithmetic tools."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING
from .agent_models import CalculationRequest, GoalPlan
from pydantic import ValidationError
from .calculation_tools import required_contribution, contribution_projection, contribution_cashflow


class CalculationInputError(ValueError):
    """Known domain constraint, safe to return to the model without its input values."""
    def __init__(self, field, code, requirement):
        self.issue={'field':field,'code':code,'requirement':requirement}
        super().__init__(requirement)


def calculation_error(exc):
    """Bounded contract feedback; never echo untrusted values, keys or exception text."""
    if isinstance(exc,CalculationInputError):
        issues=[exc.issue]
    elif isinstance(exc,ValidationError):
        allowed=set(CalculationRequest.model_fields)|set(GoalPlan.model_fields)
        issues=[]
        hints={'int_type':'Use a JSON integer, not a boolean, string or fractional number.',
               'float_type':'Use a finite nonnegative JSON number, not a boolean or string.',
               'missing':'Supply this required field.', 'extra_forbidden':'Remove unsupported fields.',
               'literal_error':'frequency must be monthly or fortnightly.',
               'finite_number':'Use a finite number.',
               'date_from_datetime_parsing':'Use a valid YYYY-MM-DD date.',
               'greater_than_equal':'Use a value at or above the schema minimum.',
               'less_than_equal':'Use a value at or below the schema maximum.'}
        for e in exc.errors(include_input=False,include_context=False,include_url=False)[:4]:
            loc='.'.join(str(v) if isinstance(v,int) else v if v in allowed else 'unsupported_field' for v in e['loc'][:4])
            issues.append({'field':loc,'code':e['type'],'requirement':hints.get(e['type'],'Follow the declared calculation schema type and bounds.')})
    else:
        issues=[{'field':'request','code':'invalid_arguments','requirement':'Follow the declared calculation schema; use only authorised goals and supported finite schedule values.'}]
    return {'error':'invalid_calculation_arguments','recoverable':True,'issues':issues,
            'instruction':'Correct these arguments within the remaining tool rounds. No calculation ref was created. Do not invent a result or reference.'}


def add_months(day, count):
    n=day.year*12+day.month-1+count
    year,month=divmod(n,12)
    return date(year,month+1,min(day.day,monthrange(year,month+1)[1]))

class PlanningTools:
    def __init__(self, context):
        self.context=context
        self.goals={g['goal_ref']:g for g in context['goals']}
        self.calculations={}

    def calculate_strategy(self, arguments):
        request=CalculationRequest.model_validate(arguments)
        refs=[p.goal_ref for p in request.plans]
        if len(refs)!=len(set(refs)) or set(refs)!=set(self.goals):
            raise CalculationInputError('plans','goal_scope','Calculate every goal_ref from the authorised context exactly once; remove foreign or duplicate refs.')
        as_of=date.fromisoformat(self.context['as_of'])
        rows=[]
        for plan in request.plans:
            goal=self.goals[plan.goal_ref]
            target=goal['target'] if plan.target is None else plan.target
            deadline=date.fromisoformat(goal['deadline']) if plan.deadline is None else plan.deadline
            if target<goal['allocated']:
                raise CalculationInputError('plans.target','below_allocation','Target must not be below the authorised goal allocation.')
            if not as_of<deadline<=add_months(as_of,120):
                raise CalculationInputError('plans.deadline','deadline_range','Deadline must be after the context as_of date and within the next ten years.')
            start=add_months(as_of,plan.pause_months)
            dates=[add_months(start,i) if plan.frequency=='monthly' else start+timedelta(days=14*i)
                   for i in range(1,121 if plan.frequency=='monthly' else 262)]
            periods=sum(d<=deadline for d in dates)
            # The existing routine is period arithmetic; its monthly label is remapped here.
            required=required_contribution(target=target,allocated=goal['allocated'],months=periods)['monthly_amount']
            projection=contribution_projection(target=target,allocated=goal['allocated'],monthly_contribution=plan.contribution,months=periods)
            remaining=max(Decimal(0),Decimal(str(target))-Decimal(str(goal['allocated'])))
            n=int((remaining/Decimal(str(plan.contribution))).to_integral_value(rounding=ROUND_CEILING)) if plan.contribution else None
            completion=(as_of if not remaining else dates[n-1] if n and n<=len(dates) else None)
            rows.append({'goal_ref':plan.goal_ref,'goal_kind':goal['kind'],'target':target,'allocated':goal['allocated'],
                'deadline':deadline.isoformat(),'contribution':plan.contribution,'frequency':plan.frequency,
                'pause_months':plan.pause_months,'priority':plan.priority,'periods':periods,
                'required_per_period':required,'monthly_equivalent':round(plan.contribution*(26/12 if plan.frequency=='fortnightly' else 1),2),
                'completion_date':completion.isoformat() if completion else None,
                'completion_horizon_limited':completion is None and plan.contribution>0,
                'projection':projection,'meets_deadline':projection['target_reached'],
                'hypothetical_goal_changes':{k:v for k,v in {'target':plan.target,'deadline':plan.deadline.isoformat() if plan.deadline else None}.items() if v is not None}})
        financial=self.context['financial_insights']; cash=financial['cashflow']; runway=cash['runway']
        balance=runway['projected_after_known_pending'] if runway else None
        funding=[b for b in financial['balances'] if b['kind']=='transaction']
        reserve=funding[0]['protected'] if len(funding)==1 else None
        immediate=sum(r['monthly_equivalent'] for r in rows if not r['pause_months'])
        ongoing=round(sum(r['monthly_equivalent'] for r in rows),2)
        impact=request.hypothetical_one_off_expense+request.hypothetical_missing_income
        cashflow=contribution_cashflow(monthly_contribution=immediate,monthly_surplus=cash['average_surplus'],
            liquid_balance=max(0,balance) if balance is not None else 0,protected_reserve=reserve,pending_expenses=impact) if balance is not None and reserve is not None else None
        # Preserve negative starting projections; the old helper requires a nonnegative input.
        if cashflow and balance<0:
            cashflow['liquid_after_pending_reserve_and_contribution']=round(cashflow['liquid_after_pending_reserve_and_contribution']+balance,2)
        ref=f'calculation-{len(self.calculations)+1}'
        result={'calculation_ref':ref,'plans':rows,'aggregate_monthly_equivalent':ongoing,
            'comfortable_capacity':cash['savings_capacity']['comfortable'],'stretch_capacity':cash['savings_capacity']['stretch'],
            'within_comfortable_capacity':ongoing<=cash['savings_capacity']['comfortable'],
            'within_stretch_capacity':ongoing<=cash['savings_capacity']['stretch'],
            'current_projected_cash_after_known_obligations':balance,'funding_reserve_known':reserve is not None,'immediate_contribution_monthly_equivalent':round(immediate,2),
            'one_month_comparison':cashflow,
            'hypothetical_additional_expense':request.hypothetical_one_off_expense,
            'hypothetical_additional_missing_income':request.hypothetical_missing_income,
            'assumptions':['Principal only: no investment returns or invented income.',
                'Regular contributions start one period after the as-of date plus the stated pause.',
                'Fortnightly monthly equivalent is annualised at 26/12; deadlines use actual 14-day dates.',
                'Known paid and unpaid expenses already enter the source context once. Additional shocks here are hypothetical only.',
                'Cashflow is a one-month comparison, not a complete future balance forecast. Multiple possible funding accounts require clarification.',
                'Goal allocations and protected reserves remain untouched. Priority is hypothetical, not an automatic funding rule.']}
        self.calculations[ref]=result
        return result
