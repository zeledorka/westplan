"""Trusted local application facade. No client-supplied financial state is accepted.

The host supplies a customer-bound provider and (later) an independent validator.
These methods are not an HTTP authentication boundary; callers must bind identity.
"""
from dataclasses import asdict
from datetime import date, datetime, timezone
from types import SimpleNamespace
from decimal import Decimal, ROUND_CEILING
from copy import deepcopy
from .goal_assessment import assess_goal, GoalSafetyError, commitments_version
from uuid import uuid4
from .schemas import Goal, Preferences, PendingExpense, money
from .context import build_planning_context
from .store import StaleState
from finstats.budgets import BudgetLimit
from finstats.categorisation import classified_transactions
from finstats.contracts import CATEGORIES
from finstats.data import normalize_merchant
from finstats.banking import banking_insights
from finstats.bills import recurring_series, subscription_id


class BankingService:
    def __init__(self, store, provider, *, proposal_validator=None):
        self.store=store
        self.provider=provider  # trusted callable(customer) -> dated financial inputs
        self.proposal_validator=proposal_validator

    def _inputs(self, customer):
        raw=self.provider(customer)
        if any(t.user_id and t.user_id!=customer for t in raw['txns']):
            raise ValueError('Provider returned another customer\'s transactions')
        return raw

    def _assemble(self, raw, state):
        as_of=raw['as_of']
        txns=classified_transactions(raw['txns'],merchant_corrections=state['corrections'],
                                     transaction_corrections=state['transaction_corrections'])
        protected=set(state['protected_categories'])|{b['category'] for b in state['budgets'].values() if b['protected']}
        goals=[Goal(g['goal_id'],g['kind'],g['target'],g['allocated'],date.fromisoformat(g['deadline'])) for g in state['goals'].values()]
        args={k:raw[k] for k in ('accounts','pending_expenses','event_evidence','planned_expenses','known_holidays') if k in raw}
        if state.get('presenter_events'):
            args['pending_expenses']=tuple(args.get('pending_expenses',()))+tuple(PendingExpense(e['event_id'],e['amount'],e['category'],date.fromisoformat(e['known_on']),date.fromisoformat(e['due'])) for e in state['presenter_events'])
        args['subscription_states']=state['subscriptions']
        if state.get('savings_designations'):
            pending=self._known_obligation_hold(raw,txns,args)
            accounts=[]
            for a in args.get('accounts',()):
                d=state['savings_designations'].get(a.account_id)
                if d:
                    # Later balance declines must remain readable as shortfalls, not
                    # silently erase the recorded reserve or invent replacement funds.
                    fields=asdict(a)
                    fields.update(protected=max(a.protected,d['protected']),goal_available=d['goal_available'])
                    # Known obligations have first claim on unreserved funds. Historical
                    # allocated savings remain visible; the checker detects any shortfall.
                    from finstats.insights import financial_insights
                    actual=next(b['balance'] for b in financial_insights(txns,as_of=as_of,accounts=(a,))['balances'] if b['account_id']==a.account_id)
                    fields['goal_available']=min(d['goal_available'],max(0,actual-fields['protected']-pending))
                    accounts.append(SimpleNamespace(**fields))
                else:accounts.append(a)
            args['accounts']=tuple(accounts)
        args.update(as_of=as_of,goals=goals,preferences=Preferences(protected_categories=tuple(sorted(protected))),
                    budget_limits=tuple(BudgetLimit(**b) for b in state['budgets'].values()),
                    feedback=state['feedback'],subscription_states=state['subscriptions'],event_states=state.get('shock_events',{}))
        return txns,args

    def planning_context(self, customer):
        raw=self._inputs(customer); revision,state=self.store.read(customer,raw['as_of'])
        return self._planning_context(raw,state)

    def _planning_context(self, raw, state):
        txns,args=self._assemble(raw,state)
        ctx=build_planning_context(txns,**args)
        from .safety import POLICY_VERSION
        ctx['financial_policy_version']=POLICY_VERSION
        ctx['funding_confirmation']={'explicit_savings_designation':bool(state.get('savings_designations')),
            'reserves_confirmed':all(d.get('reserves_confirmed') is True for d in state.get('savings_designations',{}).values()) if state.get('savings_designations') else False}
        ctx['recorded_goal_schedules']=[{'goal_ref':f'goal-{i+1}',**{k:g[k] for k in ('contribution','frequency','pause_months','schedule_as_of') if k in g}} for i,g in enumerate(state['goals'].values()) if 'contribution' in g]
        ctx['goal_assessments']=[{'goal_ref':f'goal-{i+1}',
            'status':g.get('financial_assessment',{}).get('status','unassessed'),
            'assessed_goal_version':g.get('financial_assessment',{}).get('goal_version'),
            'assessed_at':g.get('financial_assessment',{}).get('assessed_at'),
            'last_assessed_capacity':g.get('financial_assessment',{}).get('calculation',{}).get('comfortable_capacity'),
            'last_assessed_schedule':{k:g.get('financial_assessment',{}).get('schedule',{}).get(k) for k in ('contribution','frequency','deadline')},
            'creation_assessment':g.get('creation_assessment'),
            'legacy_unassessed':'creation_assessment' not in g} for i,g in enumerate(state['goals'].values())]
        # Goal versions affect approval even when a value is reverted.
        from hashlib import sha256
        import json
        ctx['goal_versions']=[{'goal_ref':f'goal-{i+1}','version':g['version']} for i,g in enumerate(state['goals'].values())]
        fingerprint=commitments_version(ctx)
        for summary,g in zip(ctx['goal_assessments'],state['goals'].values()):
            a=g.get('financial_assessment',{})
            summary['current_evidence_matches']=a.get('status')=='PASS' and a.get('goal_version')==g['version'] and a.get('commitments_version')==fingerprint
        ctx['state_version']=sha256(json.dumps({k:v for k,v in ctx.items() if k!='state_version'},sort_keys=True,allow_nan=False).encode()).hexdigest()
        return ctx

    def banking_view(self, customer):
        raw=self._inputs(customer); revision,state=self.store.read(customer,raw['as_of'])
        txns,args=self._assemble(raw,state)
        view=banking_insights(txns,as_of=raw['as_of'],accounts=args.get('accounts',()),
            protected_categories=args['preferences'].protected_categories,limits=args['budget_limits'],
            pending_expenses=args.get('pending_expenses',()),feedback=state['feedback'],subscription_states=state['subscriptions'],
            planned_expenses=args.get('planned_expenses',()),event_evidence=args.get('event_evidence',()),
            event_states=state.get('shock_events',{}),known_holidays=args.get('known_holidays',()))
        return {'revision':revision,'insights':view,'goals':list(state['goals'].values()),
                'proposals':list(state['proposals'].values()),'subscriptions':self.subscriptions(customer)}

    @staticmethod
    def _confirm(confirmed):
        if confirmed is not True:
            raise ValueError('Explicit user confirmation required')

    def _write(self, customer, expected_revision, mutate):
        raw=self._inputs(customer)
        return self.store.update(customer,as_of=raw['as_of'],expected_revision=expected_revision,mutate=mutate)

    @staticmethod
    def _known_obligation_hold(raw, txns, args):
        """Reserve only obligations not already covered by usable funding cash.

        Reuses the same dated spending/standing-payment evidence, without crediting
        future income. A pending bill is not reserved in full in both accounts.
        """
        posted={t.transaction_id for t in txns}
        pending=sum(e.amount for e in args.get('pending_expenses',()) if e.known_on<=raw['as_of'] and
                    e.status=='unpaid' and e.transaction_id not in posted)
        if not pending:return 0.0
        from finstats.dated_cashflow import cashflow_evidence
        from finstats.insights import financial_insights
        facts=financial_insights(txns,as_of=raw['as_of'],accounts=args.get('accounts',()))
        runway=facts['cashflow']['runway']
        evidence=cashflow_evidence(txns,as_of=raw['as_of'],accounts=args.get('accounts',()),
            pending_expenses=args.get('pending_expenses',()),subscription_states=args.get('subscription_states'),facts=facts)
        if not runway or not evidence.get('available'):return pending
        out=sum(e['amount'] for e in evidence['events'] if e['date']<=runway['window_end'] and
                e['kind']!='income' and not (e['kind']=='obligation' and not e['estimated']))
        days=max(0,(date.fromisoformat(runway['window_end'])-raw['as_of']).days)
        usable=max(0,evidence['starting_balance']-evidence['protected_reserve']-evidence['buffer']-
                     out-days*evidence['ordinary_daily_estimate'])
        return float(Decimal(str(max(0,pending-usable))).quantize(Decimal('.01'),rounding=ROUND_CEILING))

    def savings_funding(self, customer):
        raw=self._inputs(customer);_,state=self.store.read(customer,raw['as_of'])
        txns,args=self._assemble(raw,state)
        from finstats.insights import financial_insights
        facts=financial_insights(txns,as_of=raw['as_of'],accounts=args.get('accounts',()))
        allocated=money(sum((Decimal(str(g['allocated'])) for g in state['goals'].values()),Decimal(0)))
        obligations=self._known_obligation_hold(raw,txns,args)
        rows=[]
        for b in facts['balances']:
            if b['kind']!='savings':continue
            d=state.get('savings_designations',{}).get(b['account_id'])
            rows.append({**b,'reserves_confirmed':bool(d and d.get('reserves_confirmed')),
                'allocated_to_goals':allocated if d and d['goal_available']>0 else 0,'known_obligation_hold':obligations,
                'unallocated_goal_pool':round(max(0,b['goal_available']-(allocated if d else 0)),2),
                'uncommitted_balance':round(max(0,b['balance']-b['protected']-obligations-b['goal_available']),2) if d else None})
        return rows

    def designate_savings(self, customer, account_id, goal_available, protected, *, confirmed,
                           reserves_confirmed, expected_revision, expected_financial_version):
        self._confirm(confirmed)
        if reserves_confirmed is not True:raise ValueError('Confirm your reserve and known obligations explicitly')
        goal_available,protected=money(goal_available),money(protected)
        raw=self._inputs(customer)
        owned={a.account_id:a for a in raw.get('accounts',())}
        if account_id not in owned or owned[account_id].kind!='savings':raise ValueError('Select your own supported savings account')
        source=owned[account_id]
        if protected<source.protected:raise ValueError('Source protected reserves cannot be reduced')
        def apply(state):
            context=self._planning_context(raw,state)
            if context['state_version']!=expected_financial_version:raise StaleState('Financial context changed; review savings again')
            active=state.get('savings_designations',{})
            if any(aid!=account_id and d['goal_available']>0 for aid,d in active.items()):
                raise ValueError('This version supports one designated funding savings account per customer')
            txns,args=self._assemble(raw,state)
            from finstats.insights import financial_insights
            balance=next(b['balance'] for b in financial_insights(txns,as_of=raw['as_of'],accounts=(source,))['balances'] if b['account_id']==account_id)
            if balance is None:raise ValueError('A dated source balance is required')
            pending=self._known_obligation_hold(raw,txns,args)
            if money(goal_available+protected+pending)>money(balance):raise ValueError('Designation exceeds balance after reserves and known outstanding obligations')
            allocated=money(sum((Decimal(str(g['allocated'])) for g in state['goals'].values()),Decimal(0)))
            # Single-source workflow: existing allocations must remain backed by this account.
            if allocated>goal_available:raise ValueError('Existing goal allocations must remain backed')
            before=deepcopy(active.get(account_id))
            entry={'goal_available':goal_available,'protected':protected,'reserves_confirmed':True,
                   'balance_observed':balance,'known_obligation_hold':pending,'allocated_at_confirmation':allocated,
                   'as_of':raw['as_of'].isoformat(),'confirmed_at':datetime.now(timezone.utc).isoformat(),
                   'financial_version':context['state_version']}
            state.setdefault('savings_designations',{})[account_id]=entry
            state.setdefault('savings_designation_history',[]).append({'account_id':account_id,'before':before,'after':deepcopy(entry),
                'resulting_revision':expected_revision+1})
            revised=self._planning_context(raw,state)
            reassessments=[]
            for i,g in enumerate(state['goals'].values()):
                assessment=assess_goal(revised,f'goal-{i+1}',contribution=g.get('contribution'),
                    frequency=g.get('frequency','monthly'),pause_months=g.get('pause_months',0))
                # Reassessment never changes a goal, schedule or its prior approval.
                reassessments.append({'goal_id':g['goal_id'],'goal_version':g['version'],'assessment':assessment})
            state['latest_designation_reassessments']=reassessments
        return self._write(customer,expected_revision,apply)

    def set_budget(self, customer, limit, *, confirmed, expected_revision):
        self._confirm(confirmed)
        if not isinstance(limit,BudgetLimit) or limit.source!='user_defined':
            raise ValueError('User approval records must contain user-defined limits')
        return self._write(customer,expected_revision,lambda s:s['budgets'].update({limit.category:asdict(limit)}))

    def correct_category(self, customer, transaction_id, category, essential, *, confirmed, expected_revision):
        self._confirm(confirmed)
        if category not in CATEGORIES or not isinstance(essential,bool):
            raise ValueError('Invalid correction')
        raw=self._inputs(customer)
        row=next((t for t in raw['txns'] if t.transaction_id==transaction_id and t.date<=raw['as_of']),None)
        if row is None:
            raise ValueError('Unknown transaction')
        correction={'category':category,'essential':essential}
        def mutate(s):
            s['transaction_corrections'][transaction_id]=correction
            s['corrections'][normalize_merchant(row.description)]=correction
        return self._write(customer,expected_revision,mutate)

    def record_feedback(self, customer, *, action, insight_id=None, category=None, confirmed, expected_revision):
        self._confirm(confirmed)
        if action not in {'useful','not_useful','dismiss','reject_adjustment','protect_category'}:
            raise ValueError('Unsupported feedback')
        if category is not None and category not in CATEGORIES:
            raise ValueError('Unknown category')
        if action=='reject_adjustment' and not insight_id:
            raise ValueError('Distinct adjustment reference required')
        if action in {'reject_adjustment','protect_category'} and category is None:
            raise ValueError('Category required')
        # IDs are accepted only from the customer's generated insight collection. Already
        # suppressed insights may still receive useful/dismiss feedback via stored history.
        if action in {'useful','not_useful','dismiss'}:
            raw=self._inputs(customer); _,state=self.store.read(customer,raw['as_of'])
            txns,args=self._assemble(raw,state)
            base=banking_insights(txns,as_of=raw['as_of'],accounts=args.get('accounts',()),
                 protected_categories=args['preferences'].protected_categories,limits=args['budget_limits'],
                 pending_expenses=args.get('pending_expenses',()),subscription_states=state['subscriptions'],
                 planned_expenses=args.get('planned_expenses',()),event_evidence=args.get('event_evidence',()),
                 event_states=state.get('shock_events',{}),known_holidays=args.get('known_holidays',()))
            if insight_id not in {i['insight_id'] for i in base['personalised_insights']}:
                raise ValueError('Unknown customer insight')
        def mutate(s):
            record={'action':action,'insight_id':insight_id,'category':category}
            if action=='protect_category':
                s['protected_categories']=sorted(set(s['protected_categories'])|{category})
            # UI retries do not count as repeated rejections. Caller supplies distinct
            # proposal/insight IDs for genuinely separate discretionary adjustments.
            duplicate = record in s['feedback'] if action=='reject_adjustment' else bool(s['feedback'] and s['feedback'][-1]==record)
            if not duplicate:
                s['feedback'].append(record)
        return self._write(customer,expected_revision,mutate)

    def trigger_presenter_event(self, customer, *, goal_id, amount, kind, confirmed, expected_revision):
        self._confirm(confirmed)
        raw=self._inputs(customer)
        _,state=self.store.read(customer,raw['as_of'])
        if goal_id not in state['goals']:raise ValueError('Unknown customer goal')
        if kind not in {'car_repair','unexpected_bill'} or not 0<amount<=100000:raise ValueError('Unsupported presenter event')
        event_id=uuid4().hex
        category='Transport' if kind=='car_repair' else 'Utilities'
        event=PendingExpense(event_id,amount,category,raw['as_of'],raw['as_of'])
        def apply(s):
            s.setdefault('presenter_events',[]).append({'event_id':event.event_id,'goal_id':goal_id,'kind':kind,
                'amount':event.amount,'category':category,'known_on':event.known_on.isoformat(),'due':event.due.isoformat(),'status':'unpaid'})
            s.setdefault('presenter_notifications',[]).append({'id':event_id,'goal_id':goal_id,
                'title':'Unexpected obligation: review your goal','message':f'A confirmed unpaid {kind.replace("_"," ")} of AUD {amount:.2f} is now included in the backend projection. Goal feasibility needs reassessment.',
                'on':raw['as_of'].isoformat()})
        return self._write(customer,expected_revision,apply)

    def _assess_candidate(self, customer, state, record, *, contribution=None, frequency='monthly', pause_months=0):
        candidate=deepcopy(state)
        candidate['goals'][record['goal_id']]=deepcopy(record)
        context=self._planning_context(self._inputs(customer),candidate)
        ref=f"goal-{list(candidate['goals']).index(record['goal_id'])+1}"
        assessment=assess_goal(context,ref,contribution=contribution,frequency=frequency,pause_months=pause_months)
        if assessment['status']!='PASS':raise GoalSafetyError(assessment)
        schedule=assessment['schedule']
        record.update({k:schedule[k] for k in ('contribution','frequency','pause_months')})
        record['schedule_as_of']=schedule['schedule_as_of']
        assessment['goal_version']=record['version']
        record['financial_assessment']=assessment
        record['funding_sources']=[{'account_id':aid,'allocated':record['allocated']} for aid,d in state.get('savings_designations',{}).items() if d['goal_available']>0 and record['allocated']>0]
        candidate['goals'][record['goal_id']]=record
        context['recorded_goal_schedules']=[{'goal_ref':f'goal-{i+1}',**{k:g[k] for k in ('contribution','frequency','pause_months','schedule_as_of') if k in g}} for i,g in enumerate(candidate['goals'].values()) if 'contribution' in g]
        assessment['commitments_version']=commitments_version(context)
        return record

    def preview_goal(self, customer, goal, *, expected_revision, contribution=None, frequency='monthly', pause_months=0):
        raw=self._inputs(customer);revision,state=self.store.read(customer,raw['as_of'])
        if revision!=expected_revision:raise StaleState('State changed')
        record=asdict(goal);record.update(deadline=goal.deadline.isoformat(),version=1)
        candidate=deepcopy(state);candidate['goals'][goal.goal_id]=record
        context=self._planning_context(raw,candidate)
        ref=f"goal-{list(candidate['goals']).index(goal.goal_id)+1}"
        return assess_goal(context,ref,contribution=contribution,frequency=frequency,pause_months=pause_months)

    def create_goal(self, customer, goal, *, confirmed, expected_revision, contribution=None, frequency='monthly', pause_months=0):
        self._confirm(confirmed)
        if not isinstance(goal,Goal):
            raise ValueError('Goal input required')
        raw=self._inputs(customer)
        if goal.deadline<=raw['as_of']:
            raise ValueError('New goal deadline must be in the future')
        def mutate(s):
            if goal.goal_id in s['goals']:
                raise ValueError('Goal already exists')
            record=asdict(goal);record['deadline']=goal.deadline.isoformat();record['version']=1
            record=self._assess_candidate(customer,s,record,contribution=contribution,frequency=frequency,pause_months=pause_months)
            a=record['financial_assessment']
            record['creation_assessment']={k:deepcopy(a[k]) for k in ('status','goal_version','assessed_at','schedule','safety_assessment')}
            record['creation_assessment']['comfortable_capacity']=a['calculation']['comfortable_capacity']
            s['goals'][goal.goal_id]=record
        return self._write(customer,expected_revision,mutate)

    def _check_allocation(self, customer, state, goal):
        raw=self._inputs(customer);txns,args=self._assemble(raw,state)
        context=build_planning_context(txns,**args)
        available=sum(b['goal_available'] for b in context['financial_insights']['balances'])
        total=goal.allocated+sum(g['allocated'] for k,g in state['goals'].items() if k!=goal.goal_id)
        if total>available:
            raise ValueError('Allocations exceed trusted designated savings')

    def propose_goal_change(self, customer, goal_id, changes, *, expected_revision, source='user'):
        if source not in {'user','planner'} or not changes or set(changes)-{'target','allocated','deadline','contribution','frequency','pause_months'}:
            raise ValueError('Unsupported proposal')
        raw=self._inputs(customer); revision,state=self.store.read(customer,raw['as_of'])
        if revision!=expected_revision:
            raise StaleState('State changed')
        if goal_id not in state['goals']:
            raise ValueError('Unknown goal')
        current=state['goals'][goal_id]
        updated={**current,**changes}
        goal=Goal(goal_id,current['kind'],updated['target'],updated['allocated'],date.fromisoformat(updated['deadline']))
        if goal.deadline<=raw['as_of']:
            raise ValueError('Deadline must be in the future')
        context=self.planning_context(customer)
        from .agent_models import GoalPlan
        schedule_changes={k:v for k,v in changes.items() if k in {'contribution','frequency','pause_months'}}
        if schedule_changes:
            GoalPlan.model_validate({'goal_ref':'goal-1','contribution':updated.get('contribution',0.0),
                                     'frequency':updated.get('frequency','monthly'),'pause_months':updated.get('pause_months',0)})
        clean={k:(getattr(goal,k).isoformat() if k=='deadline' else getattr(goal,k)) for k in changes if k not in schedule_changes}
        clean.update(schedule_changes)
        if source=='planner':
            self._validate_proposal({'goal_ref':f"goal-{list(state['goals']).index(goal_id)+1}",'changes':clean},context)
        proposal={'proposal_id':uuid4().hex,'goal_id':goal_id,'goal_version':current['version'],
                  'financial_version':context['state_version'],'changes':clean,'source':source,'status':'pending','decisions':[],'created_on':raw['as_of'].isoformat()}
        self._write(customer,expected_revision,lambda s:s['proposals'].update({proposal['proposal_id']:proposal}))
        return proposal

    def _validate_proposal(self, changes, context):
        # Only a host-configured checker can validate a future AI proposal. A client
        # cannot pass `validated=True` or substitute its own financial context.
        if self.proposal_validator is None or self.proposal_validator(changes,context) is not True:
            raise ValueError('Independent validation required')

    def decide_proposal(self, customer, proposal_id, decision, *, confirmed, expected_revision):
        self._confirm(confirmed)
        if decision not in {'approve','reject','snooze'}:
            raise ValueError('Unknown decision')
        raw=self._inputs(customer)
        def mutate(s):
            p=s['proposals'].get(proposal_id)
            if not p or p['status'] not in {'pending','snoozed'}:
                raise ValueError('Proposal is not reviewable')
            if decision=='approve':
                current=s['goals'][p['goal_id']]
                context=self.planning_context(customer)
                if current['version']!=p['goal_version'] or context['state_version']!=p['financial_version']:
                    raise StaleState('Proposal no longer matches current financial/goal state')
                if p['source']=='planner':
                    self._validate_proposal({'goal_ref':f"goal-{list(s['goals']).index(p['goal_id'])+1}",'changes':p['changes']},context)
                updated={**current,**p['changes']}
                goal=Goal(updated['goal_id'],updated['kind'],updated['target'],updated['allocated'],date.fromisoformat(updated['deadline']))
                updated['version']=current['version']+1
                # A target/date/allocation edit derives a fresh required schedule unless
                # the customer explicitly proposed a contribution. Never retain stale approval.
                explicit=p['changes'].get('contribution')
                if explicit is None and set(p['changes']) <= {'frequency','pause_months'}:
                    explicit=updated.get('contribution')
                s['goals'][p['goal_id']]=self._assess_candidate(customer,s,updated,
                    contribution=explicit,frequency=updated.get('frequency','monthly'),pause_months=updated.get('pause_months',0))
            p['status']={'approve':'approved','reject':'rejected','snooze':'snoozed'}[decision]
            p['decision_on']=raw['as_of'].isoformat()
            p['decisions'].append({'decision':decision,'on':p['decision_on']})
        return self._write(customer,expected_revision,mutate)

    def subscriptions(self, customer):
        raw=self._inputs(customer);_,state=self.store.read(customer,raw['as_of'])
        txns,args=self._assemble(raw,state)
        return [{'subscription_id':subscription_id(s.account_id,s.merchant),'merchant':s.merchant,
                 'category':s.category,'typical_amount':s.typical_amount,'period_days':s.period_days,
                 'status':state['subscriptions'].get(subscription_id(s.account_id,s.merchant),{}).get('status','active')}
                for s in recurring_series(txns,raw['as_of']) if s.category=='Subscriptions' and not any(t.essential for t in s.occurrences)]

    def subscription_action(self, customer, sid, action, *, confirmed, expected_revision):
        self._confirm(confirmed)
        if action not in {'request_cancellation','confirm_cancelled','confirm_failed'}:
            raise ValueError('Unknown simulated action')
        if sid not in {s['subscription_id'] for s in self.subscriptions(customer)}:
            raise ValueError('Unknown customer subscription')
        raw=self._inputs(customer)
        def mutate(s):
            old=s['subscriptions'].get(sid,{}).get('status','active')
            if action=='request_cancellation' and old in {'active','cancellation_failed'}:
                status='cancellation_requested'
            elif action in {'confirm_cancelled','confirm_failed'} and old=='cancellation_requested':
                status='cancelled' if action=='confirm_cancelled' else 'cancellation_failed'
            else:
                raise ValueError('Invalid cancellation transition')
            s['subscriptions'][sid]={'status':status,'effective_on':raw['as_of'].isoformat(),'simulated':True}
        return self._write(customer,expected_revision,mutate)

    def search(self, customer, question):
        from finstats.search import parse_question, search_transactions
        raw=self._inputs(customer);_,state=self.store.read(customer,raw['as_of'])
        txns,_=self._assemble(raw,state)
        return search_transactions(txns,parse_question(question,as_of=raw['as_of']),as_of=raw['as_of'])

    def goal_history(self, customer):
        raw=self._inputs(customer)
        seen=set();rows=[]
        for revision,day,state in self.store.history(customer,raw['as_of']):
            for g in state['goals'].values():
                key=(g['goal_id'],g['version'])
                if key not in seen:
                    rows.append({**g,'recorded_on':day,'store_revision':revision})
                    seen.add(key)
        return rows


    def record_shock_action(self, customer, event_ref, *, action, evidence_version,
                            confirmed, expected_revision, remember_expected=False):
        from finstats.event_lifecycle import ACTIONS
        self._confirm(confirmed)
        if action not in ACTIONS or not isinstance(remember_expected,bool):
            raise ValueError('Unsupported event action')
        if remember_expected and action!='confirm_expected':
            raise ValueError('Only explicit expected confirmation can be remembered')
        raw=self._inputs(customer)
        def mutate(state):
            events=self.banking_view(customer)['insights']['shocks']
            current=next((e for e in events if e['event_ref']==event_ref),None)
            if current is None:
                raise ValueError('Unknown customer event')
            if current['evidence_version']!=evidence_version:
                raise StaleState('Event evidence changed; review the latest event before confirming')
            states=state.setdefault('shock_events',{})
            previous=states.get(event_ref,{})
            if action=='reopen' and current['lifecycle_status']=='new':
                raise ValueError('Event is already open')
            expectation=('expected' if action=='confirm_expected' else 'unexpected' if action=='confirm_unexpected'
                         else previous.get('expectedness','unknown'))
            if action=='confirm_unexpected' and current['event_type'] in {'missing_income','delayed_income','reduced_income'}:
                # This confirms surprise, not permanent loss or financial hardship.
                expectation='unexpected'
            states[event_ref]={'status':ACTIONS[action],'expectedness':expectation,
                'evidence_version':evidence_version,'known_on':raw['as_of'].isoformat(),
                'comparison_key':current.get('_comparison_key'), 'amount':current['event_amount'],
                'event_date':current['date'],'transaction_ids':current['transaction_ids'],
                'remember_expected':remember_expected if action=='confirm_expected' else False if action=='confirm_unexpected' else previous.get('remember_expected',False),
                'history':previous.get('history',[])+[{'action':action,'on':raw['as_of'].isoformat(),
                                                     'evidence_version':evidence_version}]}
        return self._write(customer,expected_revision,mutate)

    def shock_history(self, customer):
        raw=self._inputs(customer)
        _,state=self.store.read(customer,raw['as_of'])
        return [{'event_ref':ref,**{key:value[key] for key in ('status','expectedness','known_on','event_date','history')}}
                for ref,value in state.get('shock_events',{}).items()]
