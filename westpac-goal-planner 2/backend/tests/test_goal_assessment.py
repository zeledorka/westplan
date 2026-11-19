"""Mandatory admission tests: actual Python tools/checker, temporary stores, no LLM calls."""
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fastapi.testclient import TestClient
from api import create_app
from planning.demo import AS_OF, demo_contexts
from planning.schemas import Goal, AccountSnapshot, PendingExpense
from planning.store import DemoStore, StaleState
from planning.service import BankingService
from planning.goal_assessment import GoalSafetyError, assess_goal, commitments_version
from tests.test_banking import fixture_inputs
from tests.test_gemini_planner import seed_legacy_goal, MockGemini


class GoalAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DemoStore(Path(self.temp.name)/'bank.sqlite')
        self.customer='synthetic-holiday';self.raw=fixture_inputs()
        self.bank=BankingService(self.store,lambda customer:self.raw)
    def snapshot(self):return self.store.read(self.customer,AS_OF)
    def create(self,target=6000,allocated=1200,deadline=date(2027,5,31),goal_id='holiday',**kw):
        self.bank.create_goal(self.customer,Goal(goal_id,'holiday',target,allocated,deadline),
                              confirmed=True,expected_revision=self.snapshot()[0],**kw)
        return self.snapshot()[1]['goals'][goal_id]
    def approve(self,changes):
        proposal=self.bank.propose_goal_change(self.customer,'holiday',changes,expected_revision=self.snapshot()[0])
        self.bank.decide_proposal(self.customer,proposal['proposal_id'],'approve',confirmed=True,expected_revision=self.snapshot()[0])
        return self.snapshot()[1]['goals']['holiday']
    def test_feasible_holiday_persists_authoritative_schedule_and_provenance(self):
        goal=self.create();a=goal['financial_assessment']
        self.assertEqual(a['status'],'PASS');self.assertEqual(a['goal_version'],1)
        self.assertEqual(goal['contribution'],600)
        self.assertTrue(a['schedule']['meets_deadline'])
        self.assertLessEqual(a['schedule']['completion_date'],goal['deadline'])
        self.assertEqual(a['commitments_version'],commitments_version(self.bank.planning_context(self.customer)))
        self.assertEqual(goal['creation_assessment']['status'],'PASS')
        self.assertIn('financial_context',a);self.assertIn('assessed_at',a)
        self.assertEqual(a['financial_version'],a['financial_context']['state_version'])
        import hashlib,json
        self.assertEqual(a['financial_version'],hashlib.sha256(json.dumps({k:v for k,v in a['financial_context'].items() if k!='state_version'},sort_keys=True,allow_nan=False).encode()).hexdigest())
    def test_unaffordable_creation_is_revise_and_atomic(self):
        before=self.snapshot()
        with self.assertRaises(GoalSafetyError) as raised:self.create(target=100000)
        self.assertEqual(raised.exception.assessment['status'],'REVISE')
        self.assertEqual(self.snapshot(),before)
    def test_no_contribution_date_cannot_pass(self):
        with self.assertRaises(GoalSafetyError):self.create(deadline=AS_OF+timedelta(days=1))
        self.assertFalse(self.snapshot()[1]['goals'])
    def test_negative_projected_cash_blocks(self):
        self.raw['pending_expenses']=(PendingExpense('unfunded-bill',3000,'Pets',AS_OF,AS_OF),)
        with self.assertRaises(GoalSafetyError) as raised:self.create()
        self.assertEqual(raised.exception.assessment['status'],'BLOCK')
    def test_unpaid_bill_is_included_before_admission(self):
        self.raw['pending_expenses']=(PendingExpense('bill',1850,'Pets',AS_OF,AS_OF),)
        with self.assertRaises(GoalSafetyError) as raised:self.create()
        evidence=raised.exception.assessment['safety_assessment']['strategies'][0]['evidence']
        self.assertEqual(evidence['confirmed_pending_due'],1850)
        self.assertEqual(evidence['cash_after_known_obligations'],150)
        self.assertEqual(evidence['cash_after_contribution'],-450)
    def test_missing_financial_history_blocks_even_with_confirmation(self):
        self.raw['txns']=[]
        with self.assertRaises(GoalSafetyError) as raised:self.create()
        self.assertEqual(raised.exception.assessment['status'],'BLOCK')
        self.assertFalse(self.snapshot()[1]['goals'])
    def test_multiple_goals_include_commitments_and_do_not_double_capacity(self):
        first=self.create(target=3600)
        second=self.create(goal_id='second',target=1600,allocated=0)
        a=second['financial_assessment']
        self.assertEqual(a['status'],'PASS')
        self.assertEqual(a['calculation']['aggregate_monthly_equivalent'],500)
        self.assertEqual(self.snapshot()[1]['goals']['holiday']['allocated'],first['allocated'])
        before=self.snapshot()
        with self.assertRaises(GoalSafetyError):self.create(goal_id='third',target=4800,allocated=0)
        self.assertEqual(self.snapshot(),before)
    def test_existing_paused_commitments_fail_closed_without_restarting_pause(self):
        self.create(pause_months=1)
        with self.assertRaises(GoalSafetyError):self.create(goal_id='second',target=500,allocated=0)
        self.assertEqual(set(self.snapshot()[1]['goals']),{'holiday'})

    def test_unknown_legacy_commitments_fail_closed(self):
        seed_legacy_goal(self.bank,self.customer,Goal('legacy','holiday',2000,0,date(2027,5,31)),0)
        with self.assertRaises(GoalSafetyError):self.create()
        self.assertEqual(set(self.snapshot()[1]['goals']),{'legacy'})
        self.assertNotIn('financial_assessment',self.snapshot()[1]['goals']['legacy'])
    def test_target_increase_recalculates_and_preserves_creation_history(self):
        old=self.create();new=self.approve({'target':6500})
        self.assertEqual(new['contribution'],662.5)
        self.assertEqual(new['financial_assessment']['goal_version'],2)
        self.assertEqual(new['creation_assessment'],old['creation_assessment'])
        self.assertEqual(new['financial_assessment']['status'],'PASS')
    def test_shorter_deadline_and_stale_schedule_cannot_be_approved(self):
        old=self.create()
        with self.assertRaises(GoalSafetyError):self.approve({'deadline':'2026-10-31'})
        self.assertEqual(self.snapshot()[1]['goals']['holiday'],old)
        with self.assertRaises(GoalSafetyError):self.approve({'target':6500,'contribution':600.0})
        self.assertEqual(self.snapshot()[1]['goals']['holiday'],old)
    def test_explicit_frequency_and_schedule_are_assessed(self):
        goal=self.create(frequency='fortnightly')
        self.assertEqual(goal['frequency'],'fortnightly')
        self.assertEqual(goal['financial_assessment']['schedule']['periods'],17)
        self.assertTrue(goal['financial_assessment']['schedule']['meets_deadline'])
        with self.assertRaises(GoalSafetyError):self.approve({'contribution':10000.0})
    def test_stale_financial_context_rejects_manual_approval(self):
        self.create()
        p=self.bank.propose_goal_change(self.customer,'holiday',{'target':6500},expected_revision=1)
        self.raw['txns']=fixture_inputs(paid=True)['txns']
        with self.assertRaises(StaleState):self.bank.decide_proposal(self.customer,p['proposal_id'],'approve',confirmed=True,expected_revision=2)
    def test_stale_goal_version_rejects_manual_approval(self):
        self.create()
        p=self.bank.propose_goal_change(self.customer,'holiday',{'target':6500},expected_revision=1)
        self.store.update(self.customer,as_of=AS_OF,expected_revision=2,
                          mutate=lambda s:s['goals']['holiday'].update(version=2))
        with self.assertRaises(StaleState):self.bank.decide_proposal(self.customer,p['proposal_id'],'approve',confirmed=True,expected_revision=3)
    def test_shock_invalidates_freshness_without_rewriting_original_pass(self):
        goal=self.create();before=goal['financial_assessment']['commitments_version']
        self.raw['pending_expenses']=(PendingExpense('shock',100,'Pets',AS_OF,AS_OF),)
        self.assertNotEqual(before,commitments_version(self.bank.planning_context(self.customer)))
        self.assertEqual(self.snapshot()[1]['goals']['holiday']['financial_assessment']['status'],'PASS')


class DeterministicEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.contexts=demo_contexts()
    def test_paid_and_unpaid_expenses_are_not_counted_twice(self):
        for mode,pending in [('vet_paid',0),('vet_unpaid',1850)]:
            a=assess_goal(self.contexts[mode],'goal-1')
            e=a['safety_assessment']['strategies'][0]['evidence']
            self.assertEqual(e['confirmed_pending_due'],pending)
            self.assertEqual(e['cash_after_known_obligations'],150)
            self.assertEqual(e['cash_after_contribution'],-450)
            self.assertNotEqual(a['status'],'PASS')
    def test_preview_is_read_only_and_uses_actual_dates(self):
        a=assess_goal(self.contexts['normal'],'goal-1')
        self.assertEqual(a['status'],'PASS')
        self.assertEqual(a['schedule']['contribution'],600)
        self.assertEqual(a['schedule']['completion_date'],'2027-05-30')


class GoalAdmissionApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=create_app(Path(self.temp.name)/'api.sqlite',planner_transport=MockGemini(alternatives=False));self.client=TestClient(self.app)
        self.client.post('/api/session',json={'customer_id':'u003','demo_acknowledged':True})
        self.view=self.client.get('/api/overview').json()
        response=self.client.post('/api/scenario',headers={'X-Context-Scope':self.view['scope_version']},
                                  json={'scenario':'integrated_demo','reset':True,'fixture':'stable_holiday'})
        self.view=response.json()
    def post(self,path,**changes):
        return self.client.post(path,headers={'X-Context-Scope':self.view['scope_version']},json={
            'kind':'holiday','target':6000.0,'allocated':1200.0,'deadline':'2027-05-31',
            'confirmed':True,'expected_revision':self.view['revision'],**changes})
    def test_preview_does_not_persist_and_final_create_reassesses(self):
        response=self.post('/api/goals/preview');self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'PASS')
        self.assertEqual(self.client.get('/api/overview').json()['revision'],self.view['revision'])
        response=self.post('/api/goals');self.assertEqual(response.status_code,200,response.text)
        goal=response.json()['goals'][0]
        self.assertTrue(goal['assessment_current'])
        self.assertEqual(goal['financial_assessment']['status'],'PASS')
    def test_direct_api_and_confirmation_cannot_bypass_failed_assessment(self):
        response=self.post('/api/goals',target=100000.0)
        self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(response.json()['error']['code'],'goal_safety_failed')
        self.assertEqual(response.json()['error']['assessment']['status'],'REVISE')
        self.assertFalse(self.client.get('/api/overview').json()['goals'])
        self.assertEqual(self.post('/api/goals',confirmed=False).status_code,400)
    def test_unaffordable_customer_cannot_bypass_gate(self):
        self.client.post('/api/scenario',headers={'X-Context-Scope':self.view['scope_version']},json={'scenario':'customer'})
        self.view=self.client.get('/api/overview').json()
        response=self.post('/api/goals',allocated=0.0)
        self.assertEqual(response.status_code,422,response.text)
        self.assertFalse(self.client.get('/api/overview').json()['goals'])

    def test_passed_baseline_ai_draft_is_not_applied_without_explicit_sandbox_approval(self):
        created=self.post('/api/goals');self.assertEqual(created.status_code,200,created.text)
        view=created.json();goal=view['goals'][0]
        headers={'X-Context-Scope':view['scope_version']}
        response=self.client.post('/api/planner/assess',headers=headers,json={
            'goal_id':goal['goal_id'],'question':'Assess the original schedule','request_id':'baseline',
            'expected_revision':view['revision']})
        self.assertEqual(response.status_code,200,response.text)
        draft=response.json();self.assertEqual(draft['safety_assessment']['status'],'PASS')
        self.assertFalse(draft['activation_allowed'])
        unchanged=self.client.get('/api/overview').json()
        self.assertEqual(unchanged['goals'][0],goal)
        response=self.client.post('/api/presenter/drafts/'+draft['draft_id']+'/approve',headers=headers,json={
            'calculation_ref':draft['result']['recommended_ref'],'confirmed':True,'expected_revision':view['revision']})
        self.assertEqual(response.status_code,200,response.text)
        approved=response.json()['goals'][0]
        self.assertTrue(approved['assessment_current'])
        self.assertEqual(approved['creation_assessment'],goal['creation_assessment'])
        self.assertEqual(approved['financial_assessment']['goal_version'],2)
