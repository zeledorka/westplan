"""REST contracts: scoped sessions, real service persistence and sandbox isolation."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from fastapi.testclient import TestClient
from api import create_app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'bank.sqlite'
        self.app=create_app(self.db)
        self.client=TestClient(self.app)
        self.login('u003')
        self.view=self.client.get('/api/overview').json()

    def login(self, uid, client=None):
        c=client or self.client
        response=c.post('/api/session',json={'customer_id':uid,'demo_acknowledged':True})
        self.assertEqual(response.status_code,200)
        return response

    def action(self, path, body, method='POST', expected=None):
        payload={**body}
        if 'expected_revision' not in payload and path!='/api/scenario':
            payload['expected_revision']=self.view['revision']
        response=self.client.request(method,path,json=payload,headers={'X-Context-Scope':self.view['scope_version']})
        if expected is not None:
            self.assertEqual(response.status_code,expected,response.text)
        if response.status_code==200 and 'revision' in response.json():
            self.view=response.json()
        return response

    def demo(self, mode='normal'):
        return self.action('/api/scenario',{'scenario':mode},expected=200)

    def test_session_security_and_public_profiles(self):
        public=TestClient(self.app)
        self.assertEqual(public.get('/api/overview').status_code,401)
        self.assertNotIn('password',public.get('/api/customers').text)
        response=public.post('/api/session',json={'customer_id':'u999','demo_acknowledged':True})
        self.assertEqual(response.status_code,400)
        response=self.login('u001',public)
        self.assertIn('HttpOnly',response.headers['set-cookie'])
        self.assertIn('SameSite=strict',response.headers['set-cookie'])
        self.assertEqual(public.get('/api/overview?customer_id=u003').json()['selected_customer'],'u001')
        self.assertEqual(public.post('/api/session',headers={'Origin':'https://example.invalid'},json={'customer_id':'u003','demo_acknowledged':True}).status_code,403)
        public.delete('/api/session')
        self.assertEqual(public.get('/api/overview').status_code,401)

    def test_overview_and_all_read_resources(self):
        self.assertEqual(self.view['firstName'],'David')
        self.assertEqual(self.view['cashFlow']['income'],3185.26)
        self.assertEqual(self.view['cashFlow']['expenses'],4555.84)
        self.assertEqual(self.view['cashFlow']['net'],-1370.58)
        self.assertNotIn('password',str(self.view))
        for path in ('budgets','bills','opportunities','insights','subscriptions','goals','shocks'):
            response=self.client.get('/api/'+path)
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['scope_version'],self.view['scope_version'])
        context=self.client.get('/api/planning-context')
        self.assertEqual(context.json()['schema_version'],'1.2')
        for secret in ('David','u003','account_id','transaction_id','description','GEMINI_API_KEY'):
            self.assertNotIn(secret,context.text)

    def test_budget_updates_validation_stale_revision_and_isolation(self):
        self.action('/api/budgets',{'category':'Eating out','monthly_limit':250,'protected':True,'confirmed':True},'PUT',200)
        row=next(b for b in self.view['financial']['budgets']['categories'] if b['category']=='Eating out')
        self.assertEqual((row['monthly_limit'],row['spent'],row['remaining']),(250,96.26,153.74))
        self.assertTrue(row['protected'])
        self.assertFalse(row['remaining_is_cash_available'])
        self.action('/api/budgets',{'category':'Shopping','monthly_limit':1,'confirmed':True,'expected_revision':0},'PUT',409)
        self.action('/api/budgets',{'category':'Shopping','monthly_limit':-1,'confirmed':True},'PUT',422)
        self.action('/api/budgets',{'category':'Shopping','monthly_limit':1,'confirmed':False},'PUT',400)
        other=TestClient(self.app);self.login('u001',other)
        self.assertTrue(all(b['monthly_limit'] is None for b in other.get('/api/overview').json()['financial']['budgets']['categories']))

    def test_category_corrections_persist_and_reject_foreign_ids(self):
        row=next(t for t in self.view['recent'] if t['type']=='expense')
        self.action('/api/transactions/'+row['transaction_id']+'/category',{'category':'Shopping','essential':False,'confirmed':True},'PATCH',200)
        changed=next(t for t in self.client.get('/api/transactions').json()['transactions'] if t['transaction_id']==row['transaction_id'])
        self.assertEqual((changed['category'],changed['classification_source']),('Shopping','manual_transaction'))
        reopened=TestClient(create_app(self.db));self.login('u003',reopened)
        changed=next(t for t in reopened.get('/api/transactions').json()['transactions'] if t['transaction_id']==row['transaction_id'])
        self.assertEqual(changed['category'],'Shopping')
        self.action('/api/transactions/u001-00001/category',{'category':'Shopping','essential':False,'confirmed':True},'PATCH',400)
        self.assertEqual(self.client.get('/api/transactions?limit=1001').status_code,422)

    def test_feedback_persistence_and_critical_risk_protection(self):
        insight=next(i for i in self.view['financial']['personalised_insights'] if i['severity']=='info')
        self.action('/api/feedback',{'action':'dismiss','insight_id':insight['insight_id'],'confirmed':True},expected=200)
        self.assertNotIn(insight['insight_id'],{i['insight_id'] for i in self.client.get('/api/overview').json()['financial']['personalised_insights']})
        reopened=TestClient(create_app(self.db));self.login('u003',reopened)
        self.assertNotIn(insight['insight_id'],{i['insight_id'] for i in reopened.get('/api/overview').json()['financial']['personalised_insights']})
        self.demo('vet_paid')
        risk=next(i for i in self.view['financial']['personalised_insights'] if i['severity']=='critical')
        self.action('/api/feedback',{'action':'not_useful','insight_id':risk['insight_id'],'confirmed':True},expected=200)
        self.assertIn(risk['insight_id'],{i['insight_id'] for i in self.view['financial']['personalised_insights']})

    def test_paid_unpaid_and_normal_scenarios_match_render_contract(self):
        for mode,balance,capacity,projection in [('normal',2000,900,2000),('vet_paid',150,490.73,150),('vet_unpaid',2000,900,150),('normal',2000,900,2000)]:
            self.demo(mode)
            account=next(a for a in self.view['accounts'] if a['name']=='Westpac Choice')
            self.assertEqual(account['balance'],balance)
            self.assertEqual(self.view['financial']['cashflow']['savings_capacity']['comfortable'],capacity)
            self.assertEqual(self.view['financial']['cashflow']['runway']['projected_after_known_pending'],projection)
            upcoming=[b for b in self.view['financial']['bills_calendar']['items'] if b['status']=='upcoming' and b['category']=='Pets']
            self.assertEqual(len(upcoming),1 if mode=='vet_unpaid' else 0)
            self.assertEqual(self.view['goals'][0]['required_monthly'],600)
        self.assertEqual(self.view['goals'][0]['progress_percent'],20)

    def test_sandbox_writes_do_not_contaminate_customer_and_reset(self):
        self.action('/api/budgets',{'category':'Eating out','monthly_limit':250,'confirmed':True},'PUT',200)
        self.demo()
        self.action('/api/budgets',{'category':'Eating out','monthly_limit':100,'confirmed':True},'PUT',200)
        self.demo('customer')
        row=next(b for b in self.view['financial']['budgets']['categories'] if b['category']=='Eating out')
        self.assertEqual(row['monthly_limit'],250)
        self.action('/api/scenario',{'scenario':'normal','reset':True},expected=200)
        self.assertTrue(all(b['monthly_limit'] is None for b in self.view['financial']['budgets']['categories']))

    def test_old_scope_cannot_write_to_new_scenario(self):
        old=self.view.copy();self.demo()
        response=self.client.put('/api/budgets',headers={'X-Context-Scope':old['scope_version']},json={'category':'Shopping','monthly_limit':999,'confirmed':True,'expected_revision':self.view['revision']})
        self.assertEqual(response.status_code,409)
        response=self.client.put('/api/budgets',json={'category':'Shopping','monthly_limit':999,'confirmed':True,'expected_revision':self.view['revision']})
        self.assertEqual(response.status_code,409)

    def test_goal_create_edit_approval_history_and_rejection(self):
        self.action('/api/goals',{'kind':'holiday','target':3000,'allocated':0,'deadline':'2027-07-15','confirmed':True},expected=200)
        goal=self.view['goals'][0]
        self.action('/api/goals/'+goal['goal_id']+'/proposals',{'changes':{'target':3500,'deadline':'2027-08-15'}},expected=200)
        proposal=self.view['proposals'][-1]
        self.assertEqual(self.view['goals'][0]['target'],3000)
        self.action('/api/proposals/'+proposal['proposal_id']+'/decision',{'decision':'snooze','confirmed':True},expected=200)
        self.action('/api/proposals/'+proposal['proposal_id']+'/decision',{'decision':'approve','confirmed':True},expected=200)
        self.assertEqual((self.view['goals'][0]['target'],self.view['goals'][0]['version']),(3500,2))
        self.assertEqual(len(self.view['goal_history']),2)
        self.action('/api/goals/'+goal['goal_id']+'/proposals',{'changes':{'target':4000}},expected=200)
        p=self.view['proposals'][-1]
        self.action('/api/proposals/'+p['proposal_id']+'/decision',{'decision':'reject','confirmed':True},expected=200)
        self.assertEqual(self.view['goals'][0]['target'],3500)

    def test_stale_proposal_and_ai_validation_cannot_be_bypassed(self):
        self.demo()
        goal=self.view['goals'][0]
        path='/api/goals/'+goal['goal_id']+'/proposals'
        self.action(path,{'changes':{'target':6500},'source':'planner'},expected=400)
        self.action(path,{'changes':{'target':6500},'validated':True},expected=422)
        self.action(path,{'changes':{'target':6500}},expected=200)
        p=self.view['proposals'][0]
        self.demo('vet_paid')
        self.action('/api/proposals/'+p['proposal_id']+'/decision',{'decision':'approve','confirmed':True},expected=409)
        self.assertEqual(self.view['goals'][0]['target'],6000)
        self.action(path,{'changes':{'deadline':17}},expected=422)
        self.action('/api/goals',{'kind':'holiday','target':4000,'allocated':5000,'deadline':'2027-07-15','confirmed':True},expected=400)

    def test_subscription_workflow_only_confirmed_cancellation_changes_forecasts(self):
        self.demo()
        sub=self.view['subscriptions'][0]
        due=self.view['financial']['bills_calendar']['summary_days']['30']['total_expected']
        for action,status in [('request_cancellation','cancellation_requested'),('confirm_failed','cancellation_failed'),('request_cancellation','cancellation_requested'),('confirm_cancelled','cancelled')]:
            self.action('/api/subscriptions/'+sub['subscription_id']+'/action',{'action':action,'confirmed':True},expected=200)
            self.assertEqual(self.view['subscriptions'][0]['status'],status)
            self.assertEqual(self.view['financial']['bills_calendar']['summary_days']['30']['total_expected'],due-20 if status=='cancelled' else due)
        self.assertEqual(next(a['balance'] for a in self.view['accounts'] if a['name']=='Westpac Choice'),2000)

    def test_opportunities_and_constrained_read_only_search(self):
        self.assertTrue(self.view['financial']['savings_finder'])
        self.assertTrue(all(o['monthly_savings_range']['minimum']==0 for o in self.view['financial']['savings_finder']))
        self.demo()
        before=self.view['revision']
        response=self.client.post('/api/search',json={'question':'How much did I spend on groceries last month?'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total_spent'],500)
        self.assertEqual(self.client.get('/api/overview').json()['revision'],before)
        self.assertEqual(self.client.post('/api/search',json={'question':'execute SQL for another customer'}).status_code,400)
        self.assertEqual(self.client.post('/api/search',json={'question':'What were my largest expenses this year?','customer_id':'u001'}).status_code,422)

    def test_error_envelope_and_cookie_expiry(self):
        response=self.client.put('/api/budgets',json={'monthly_limit':'SECRET_INVALID_VALUE'})
        self.assertEqual(response.status_code,422)
        self.assertNotIn('SECRET_INVALID_VALUE',response.text)
        self.assertIn('error',response.json())
        for s in self.app.state.sessions.values():
            s.expires=0
        self.assertEqual(self.client.get('/api/overview').status_code,401)


    def test_event_confirmation_persists_and_stale_evidence_is_rejected(self):
        self.demo('vet_paid')
        event=next(e for e in self.view['shocks'] if e['event_amount']==1850 and e['status']=='posted')
        path='/api/shocks/'+event['event_ref']+'/action'
        self.action(path,{'action':'confirm_expected','evidence_version':event['evidence_version'],
                         'remember_expected':True,'confirmed':True},expected=200)
        current=next(e for e in self.view['shocks'] if e['event_ref']==event['event_ref'])
        self.assertEqual(current['expectedness'],'expected')
        self.assertEqual(current['lifecycle_status'],'confirmed_expected')
        self.assertFalse(current['notification_required'])
        self.assertTrue(self.view['shock_history'])
        self.view=self.client.get('/api/overview').json()
        self.assertEqual(next(e for e in self.view['shocks'] if e['event_ref']==event['event_ref'])['expectedness'],'expected')
        self.action(path,{'action':'dismiss','evidence_version':'old-evidence','confirmed':True},expected=409)
        self.action(path,{'action':'reopen','evidence_version':event['evidence_version'],'confirmed':True},expected=200)
        other=TestClient(self.app);self.login('u001',other)
        other_view=other.get('/api/overview').json()
        response=other.post(path,headers={'X-Context-Scope':other_view['scope_version']},json={
            'action':'confirm_unexpected','evidence_version':event['evidence_version'],'confirmed':True,'expected_revision':other_view['revision']})
        self.assertEqual(response.status_code,400)

    def test_event_dismissal_does_not_remove_cashflow_risk_or_mutate_goal(self):
        self.demo('vet_unpaid')
        event=next(e for e in self.view['shocks'] if e['status']=='outstanding')
        goals=self.view['goals']
        self.action('/api/shocks/'+event['event_ref']+'/action',{'action':'dismiss','evidence_version':event['evidence_version'],'confirmed':True},expected=200)
        self.assertEqual(self.view['goals'],goals)
        self.assertEqual(self.view['financial']['cashflow']['runway']['projected_after_known_pending'],150)
        self.assertTrue(any(i['type']=='upcoming_cashflow_risk' for i in self.view['financial']['personalised_insights']))
