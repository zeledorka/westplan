"""Trusted local application facade. No client-supplied financial state is accepted.

The host supplies a customer-bound provider and (later) an independent validator.
These methods are not an HTTP authentication boundary; callers must bind identity.
"""
from dataclasses import asdict
from datetime import date
from uuid import uuid4
from .schemas import Goal, Preferences
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
        args.update(as_of=as_of,goals=goals,preferences=Preferences(protected_categories=tuple(sorted(protected))),
                    budget_limits=tuple(BudgetLimit(**b) for b in state['budgets'].values()),
                    feedback=state['feedback'],subscription_states=state['subscriptions'],event_states=state.get('shock_events',{}))
        return txns,args

    def planning_context(self, customer):
        raw=self._inputs(customer); revision,state=self.store.read(customer,raw['as_of'])
        txns,args=self._assemble(raw,state)
        ctx=build_planning_context(txns,**args)
        # Goal versions affect approval even when a value is reverted.
        from hashlib import sha256
        import json
        ctx['goal_versions']=[{'goal_ref':f'goal-{i+1}','version':g['version']} for i,g in enumerate(state['goals'].values())]
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

    def create_goal(self, customer, goal, *, confirmed, expected_revision):
        self._confirm(confirmed)
        if not isinstance(goal,Goal):
            raise ValueError('Goal input required')
        raw=self._inputs(customer)
        if goal.deadline<=raw['as_of']:
            raise ValueError('New goal deadline must be in the future')
        def mutate(s):
            if goal.goal_id in s['goals']:
                raise ValueError('Goal already exists')
            self._check_allocation(customer,s,goal)
            record=asdict(goal);record['deadline']=goal.deadline.isoformat();record['version']=1
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
        if source not in {'user','planner'} or not changes or set(changes)-{'target','allocated','deadline'}:
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
        clean={k:getattr(goal,k).isoformat() if k=='deadline' else getattr(goal,k) for k in changes}
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
                self._check_allocation(customer,s,goal)
                updated['version']=current['version']+1
                s['goals'][p['goal_id']]=updated
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
