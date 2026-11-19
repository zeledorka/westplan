"""Source-labelled facts over existing outputs; no financial engine or recommendation policy."""
from datetime import date


def financial_facts(context, calculations):
    currency=context['currency'];as_of=context['as_of']
    history={'basis':'observed_monthly_average',
             'months':[row['month'] for row in context['financial_insights']['cashflow']['monthly']],
             'coverage':'Complete-month buckets inferred from transaction boundaries, not guaranteed statement coverage.'}
    cash=context['financial_insights']['cashflow'];runway=cash.get('runway')
    result={'version':1,'as_of':as_of,'currency':currency,'context':[],'obligations':[], 'calculations':{}}
    def fact(metric,value,unit,period,meaning,source,ref=None,assumptions=()):
        return {'metric':metric,'value':value,'unit':unit,'period':period,'meaning':meaning,
                'source':source,'calculation_ref':ref,'assumptions':{'source':ref+'.assumptions'} if ref else []}
    def add(metric,value,unit,period,meaning,source):
        if value is not None:result['context'].append(fact(metric,value,unit,period,meaning,source))
    add('historical_monthly_surplus',cash['average_surplus'],currency,history,'Historical net monthly flow before proposed savings; not cash held today.','financial_insights.cashflow.average_surplus')
    add('monthly_income',context['financial_insights']['income']['monthly_average'],currency,history,'Historical monthly income, not guaranteed future income.','financial_insights.income.monthly_average')
    for name in ('monthly_essential','monthly_discretionary'):
        add(name,context['financial_insights']['spending'][name],currency,history,'Historical monthly expense component.','financial_insights.spending.'+name)
    if runway:
        window={'as_of':as_of,'window_end':runway['window_end'],'basis':'existing_runway_projection'}
        for metric,key,meaning in [
            ('current_cash_balance','balance','Current model funding-account balance; not unrestricted savings or projected cash.'),
            ('projected_cash_before_pending','projected_balance','Runway cash projection before additional known pending bills.'),
            ('cash_after_known_obligations','projected_after_known_pending','Runway projection including confirmed pending obligations once.'),
            ('confirmed_pending_due','additional_known_pending_due','Confirmed unpaid amount due in this projection window.'),
            ('recurring_bills_due','recurring_bills_due','Recurring obligations already included by the runway engine.')]:
            add(metric,runway.get(key),currency,{'as_of':as_of} if metric=='current_cash_balance' else window,meaning,'financial_insights.cashflow.runway.'+key)
    for event in context.get('shocks',[]):
        if event['status'] not in {'posted','outstanding','anticipated'} or event.get('event_amount') is None:continue
        impact=event['impact'];ref=event['event_ref']
        result['obligations'].append({'event_ref':ref,'status':event['status'],
            'facts':[fact(metric,value,unit,{'as_of':as_of,'event_date':event['date']},meaning,'shocks.'+ref+'.'+source)
                for metric,value,unit,meaning,source in [
                    ('expense_amount',event['event_amount'],currency,'Observed/declared expense amount, not an additional debit.','event_amount'),
                    ('already_in_balance',impact.get('already_in_balance'),'boolean','Whether posted expense already affects the source balance.','impact.already_in_balance'),
                    ('included_in_projection',impact.get('already_in_projection'),'boolean','Whether pending expense is already included in projected obligations.','impact.already_in_projection'),
                    ('additional_future_debit',impact.get('additional_future_debit'),currency,'Existing engine future-debit attribution; never subtract it again from a projection that already includes it.','impact.additional_future_debit')]
                if value is not None]})
    for ref,calc in calculations.items():
        assumptions=calc['assumptions']
        period={'as_of':as_of,'projection_window_end':runway['window_end'] if runway else None,
                'basis':'one_month_contribution_comparison_on_existing_runway; not a full future balance forecast'}
        rows=[]
        def calc_fact(metric,value,unit,meaning,source,when=period):
            if value is not None:rows.append(fact(metric,value,unit,when,meaning,source,ref,assumptions))
        for metric,key,meaning in [
            ('comfortable_capacity','comfortable_capacity','Historical comfortable monthly contribution estimate, not available cash.'),
            ('stretch_capacity','stretch_capacity','Historical stretch monthly contribution estimate, not guaranteed affordability.'),
            ('planned_monthly_contribution','aggregate_monthly_equivalent','Ongoing monthly equivalent including paused schedules.'),
            ('immediate_monthly_contribution','immediate_contribution_monthly_equivalent','Legacy scalar monthly equivalent for unpaused schedules; not a debit at the assessment date.'),
            ('cash_after_known_obligations','current_projected_cash_after_known_obligations','Projection after known obligations, before proposed contribution and reserve comparison.'),
            ('within_comfortable_capacity','within_comfortable_capacity','Contribution capacity comparison only, not a cash-liquidity guarantee.'),
            ('within_stretch_capacity','within_stretch_capacity','Contribution capacity comparison only.')]:
            calc_fact(metric,calc.get(key),'boolean' if metric.startswith('within_') else currency,meaning,ref+'.'+key,
                      history if metric in {'comfortable_capacity','stretch_capacity'} else period)
        if calc['one_month_comparison']:
            for metric,key,meaning in [
                ('monthly_surplus_after_contribution','monthly_surplus_after_contribution','Monthly net-flow residual; can be positive while cash balance is negative.'),
                ('legacy_scalar_cash_comparison','liquid_after_pending_reserve_and_contribution','Legacy non-dated diagnostic; not liquidity evidence for approval.')]:
                calc_fact(metric,calc['one_month_comparison'][key],currency,meaning,ref+'.one_month_comparison.'+key)
        dated=calc.get('dated_cashflow',{})
        if dated.get('available'):
            calc_fact('cash_after_contribution',dated['minimum_cash'],currency,
                'Minimum dated cash after scheduled contributions, obligations, reserves and estimated ordinary spending; bounded forecast.',
                ref+'.dated_cashflow.minimum_cash',{'as_of':as_of,'minimum_date':dated['minimum_date'],'through':dated['end'],'basis':'Bounded dated projection; not a full future balance forecast'})
        elif 'dated_cashflow_evidence' not in context and calc['one_month_comparison']:
            calc_fact('cash_after_contribution',calc['one_month_comparison']['liquid_after_pending_reserve_and_contribution'],currency,
                'Historical legacy scalar comparison; no current dated evidence.',ref+'.one_month_comparison.liquid_after_pending_reserve_and_contribution')
        goals={}
        for plan in calc['plans']:
            goal=plan['goal_ref'];completion=plan['completion_date'];deadline=plan['deadline']
            relation='unknown' if completion is None else 'before' if date.fromisoformat(completion)<date.fromisoformat(deadline) else 'after' if completion>deadline else 'on'
            goals[goal]=[fact(metric,value,unit,{'as_of':as_of,'deadline':deadline,'frequency':plan['frequency'],'pause_months':plan['pause_months']},meaning,ref+'.plans.'+goal+'.'+key,ref,assumptions)
                for metric,key,value,unit,meaning in [
                    ('goal_target','target',plan['target'],currency,'Calculated target under this hypothetical strategy.'),
                    ('goal_savings','allocated',plan['allocated'],currency,'Existing designated allocation, not all savings.'),
                    ('planned_contribution','contribution',plan['contribution'],currency,'Contribution per declared frequency after stated pause.'),
                    ('contribution_frequency','frequency',plan['frequency'],'frequency','Declared schedule period.'),
                    ('required_contribution','required_per_period',plan['required_per_period'],currency,'Required per-period contribution for the requested deadline.'),
                    ('completion_date','completion_date',completion,'date','Calculated completion under this strategy, not the requested deadline.'),
                    ('goal_deadline','deadline',deadline,'date','Requested or explicitly calculated hypothetical deadline.'),
                    ('deadline_relation','completion_date/deadline',relation,'relationship','Python date comparison: before/on/after/unknown.'),
                    ('meets_deadline','meets_deadline',plan['meets_deadline'],'boolean','Schedule feasibility only; not affordability.')]
                if value is not None]
        result['calculations'][ref]={'assumptions':list(assumptions),'facts':rows,'goals':goals}
    return result
