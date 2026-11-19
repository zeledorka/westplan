"""Atomic human approval of independently passing calculations in a demo namespace only."""
from copy import deepcopy
import json
import sqlite3
import time
from datetime import datetime, timezone
from .goal_assessment import commitments_version
from .safety import assess_draft
from .store import StaleState


def approve_demo(bank, planner, customer, draft_id, calculation_ref, *, expected_revision, actor):
    # The HTTP route additionally requires an authenticated integrated_demo session and scope.
    if not customer.startswith('sandbox-'):
        raise ValueError('Approval is restricted to the isolated presenter sandbox')
    raw=bank._inputs(customer)
    revision,state=bank.store.read(customer,raw['as_of'])
    context=bank.planning_context(customer)
    with sqlite3.connect(bank.store.path,timeout=10) as db:
        db.execute('BEGIN IMMEDIATE')
        latest=db.execute('SELECT revision,payload FROM snapshots WHERE customer=? ORDER BY revision DESC LIMIT 1',(customer,)).fetchone()
        if not latest or latest[0]!=expected_revision or revision!=expected_revision:
            raise StaleState('Financial state changed; reassess before approval')
        state=json.loads(latest[1])
        row=db.execute('SELECT request_id,payload FROM planner_turns WHERE customer=? AND json_extract(payload,\'$.draft.draft_id\')=?',(customer,draft_id)).fetchone()
        if not row:raise ValueError('Unknown customer draft')
        record=json.loads(row[1]);draft=record.get('draft',{})
        if record.get('status')!='draft_unverified':raise ValueError('Draft is not reviewable')
        if record.get('review_decision') or draft.get('review_decision'):
            raise ValueError('This draft already has a human decision')
        if draft.get('safety_assessment',{}).get('status')!='PASS':
            raise ValueError('The completed assessment must PASS before human approval')
        goal_id=record['goal_id']
        if goal_id not in state['goals'] or draft.get('financial_version')!=context['state_version']:
            raise StaleState('Draft is stale; run a new assessment')
        if draft.get('goal_versions')!={key:g['version'] for key,g in state['goals'].items()}:
            raise StaleState('Goal versions changed; reassess')
        goal_ref=f'goal-{list(state["goals"]).index(goal_id)+1}'
        if draft.get('selected_goal_ref')!=goal_ref:raise ValueError('Selected goal mismatch')
        ledger=draft.get('baseline_calculations',{})
        assessment=assess_draft(draft,context,ledger,expected_goal_ref=goal_ref)
        if assessment['status']!='PASS':raise ValueError('Independent safety checks must PASS; BLOCK and REVISE cannot be approved')
        offered={s['calculation_ref'] for s in draft['result']['strategies']}
        if calculation_ref not in offered or calculation_ref not in draft.get('calculations',{}):
            raise ValueError('Select an offered authoritative calculation')
        if not any(s['calculation_ref']==calculation_ref and s['status']=='PASS' for s in assessment['strategies']):
            raise ValueError('Selected option did not pass independent checks')
        calculation=ledger[calculation_ref]
        plan=next((p for p in calculation['plans'] if p['goal_ref']==goal_ref),None)
        if not plan:raise ValueError('Selected calculation has no authorised goal schedule')
        # Partial application must not depend on unapproved changes to other goals.
        for other in calculation['plans']:
            if other['goal_ref']==goal_ref:continue
            index=int(other['goal_ref'].split('-')[1])-1
            existing=list(state['goals'].values())[index]
            if any(existing.get(k)!=other.get(k) for k in ('target','deadline','contribution','frequency','pause_months')):
                raise ValueError('This option changes other goals; partial approval is unsupported')
        before=deepcopy(state['goals'][goal_id])
        if plan['allocated']!=before['allocated']:raise ValueError('Goal allocation cannot change')
        after={**before,**{k:plan[k] for k in ('target','deadline','contribution','frequency','pause_months')},
               'version':before['version']+1,'schedule_as_of':plan['schedule_as_of']}
        after['financial_assessment']={'status':'PASS','goal_version':after['version'],
            'safety_assessment':assessment,'calculation':deepcopy(calculation),'calculation_ref':calculation_ref,
            'schedule':deepcopy(plan),'financial_version':context['state_version'],
            'financial_context':deepcopy(context),'assessed_at':datetime.now(timezone.utc).isoformat(),
            'assumptions':calculation['assumptions']}
        state['goals'][goal_id]=after
        updated_context=deepcopy(context)
        updated_context['goals']=[{k:g[k] for k in ('goal_ref','kind','target','allocated','deadline')} for g in context['goals']]
        selected=next(g for g in updated_context['goals'] if g['goal_ref']==goal_ref)
        selected.update({k:after[k] for k in ('target','deadline','allocated')})
        updated_context['recorded_goal_schedules']=[{'goal_ref':f'goal-{i+1}',**{k:g[k] for k in ('contribution','frequency','pause_months','schedule_as_of') if k in g}} for i,g in enumerate(state['goals'].values()) if 'contribution' in g]
        after['financial_assessment']['commitments_version']=commitments_version(updated_context)
        decision={'decision':'approved','by':actor,'at':time.time(),'draft_id':draft_id,
                  'calculation_ref':calculation_ref,'before':before,'after':after,
                  'safety_policy_version':assessment['policy_version'],'sandbox_only':True}
        state['goals'][goal_id]=after
        state.setdefault('demo_approval_history',[]).append(decision)
        state.setdefault('presenter_notifications',[]).append({'id':'approval-'+draft_id,'goal_id':goal_id,
            'title':'AI adjustment approved by you','message':'The selected Python-calculated schedule was applied in this isolated sandbox.',
            'on':context['as_of']})
        record['review_decision']=decision
        record['draft']={**draft,'review_decision':decision}  # Production activation_allowed remains false.
        db.execute('INSERT INTO snapshots VALUES (?,?,?,?)',(customer,expected_revision+1,context['as_of'],json.dumps(state,allow_nan=False)))
        db.execute('UPDATE planner_turns SET payload=? WHERE customer=? AND request_id=?',
                   (json.dumps(record,allow_nan=False),customer,row[0]))
        return decision
