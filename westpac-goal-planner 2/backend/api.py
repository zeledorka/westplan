"""Local synthetic banking REST adapter. Run: python -m uvicorn api:app --port 8000."""
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Literal, Annotated
from uuid import uuid4
import json
import os
import secrets
import time
import sqlite3

from fastapi import FastAPI, Request, Response, HTTPException, Depends, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt
from finstats import load_transactions, monthly_flows
from finstats.budgets import BudgetLimit
from finstats.categorisation import classify_transaction
from finstats.contracts import CATEGORIES
from planning.schemas import AccountSnapshot, Goal, PendingExpense, PostedEventEvidence
from planning.service import BankingService
from planning.store import DemoStore, StaleState
from planning.demo import demo_transactions, AS_OF
from planning.calculation_tools import required_contribution
from planning.gemini import GeminiTransport, PlannerError
from planning.providers import configured_transport
from planning.planner_service import PlannerService

ROOT = Path(__file__).resolve().parent
Mode = Literal['customer', 'normal', 'vet_paid', 'vet_unpaid']
Revision = Annotated[StrictInt, Field(ge=0)]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid')


class SessionInput(Input):
    customer_id: str
    demo_acknowledged: StrictBool


class ScenarioInput(Input):
    scenario: Mode
    reset: StrictBool = False


class Mutation(Input):
    expected_revision: Revision
    confirmed: StrictBool


class BudgetInput(Mutation):
    category: str
    monthly_limit: Amount
    protected: StrictBool = False


class CorrectionInput(Mutation):
    category: str
    essential: StrictBool


class FeedbackInput(Mutation):
    action: Literal['useful','not_useful','dismiss','protect_category','reject_adjustment']
    insight_id: str | None = None
    category: str | None = None


class GoalInput(Mutation):
    kind: Literal['holiday','emergency_fund','purchase','other']
    target: Amount
    allocated: Amount = 0
    deadline: date


class GoalChanges(Input):
    target: Amount | None = None
    allocated: Amount | None = None
    deadline: date | None = None


class ProposalInput(Input):
    expected_revision: Revision
    changes: GoalChanges
    source: Literal['user','planner'] = 'user'


class DecisionInput(Mutation):
    decision: Literal['approve','reject','snooze']


class SubscriptionInput(Mutation):
    action: Literal['request_cancellation','confirm_cancelled','confirm_failed']


class ShockActionInput(Mutation):
    action: Literal['acknowledge','confirm_expected','confirm_unexpected','dismiss','resolve','reopen']
    evidence_version: str
    remember_expected: StrictBool = False


class SearchInput(Input):
    question: Annotated[str, Field(min_length=1,max_length=240)]


@dataclass
class Session:
    customer: str
    expires: float
    scenario: str = 'customer'
    scope: str = field(default_factory=lambda: uuid4().hex)
    sandbox: str = field(default_factory=lambda: 'sandbox-'+uuid4().hex)
    lock: RLock = field(default_factory=RLock)


class PlannerInput(Input):
    goal_id: str
    question: Annotated[str, Field(min_length=1,max_length=1600)]
    request_id: Annotated[str, Field(min_length=1,max_length=80)]
    expected_revision: Revision
    session_id: str | None = None
    protected_categories: list[str] = Field(default_factory=list,max_length=24)
    background: StrictBool = False


class PlannerRetryInput(Input):
    expected_revision: Revision


def create_app(db_path=None, *, planner_transport=None):
    db_path = Path(db_path or os.environ.get('BANKING_DB', ROOT/'.runtime/banking.sqlite'))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    store = DemoStore(db_path)
    planner = PlannerService(store,planner_transport or configured_transport())
    app_planner = planner
    profiles = {u['user_id']:u for u in json.loads((ROOT/'data/mock_users.json').read_text())}
    ledger = {uid:load_transactions(ROOT/'data/mock_transactions.json', uid) for uid in profiles}
    sessions = {}
    app = FastAPI(title='Synthetic Banking Demo', version='1.0')
    app.state.store, app.state.sessions = store, sessions
    app.state.planner = app_planner
    allowed_origins = set(os.environ.get('BANKING_ORIGINS','http://localhost:5173,http://127.0.0.1:5173').split(','))

    @app.middleware('http')
    async def local_boundary(request, call_next):
        origin = request.headers.get('origin')
        own_origin = str(request.base_url).rstrip('/')
        if request.method not in {'GET','HEAD','OPTIONS'} and origin and origin not in allowed_origins|{own_origin}:
            return JSONResponse({'error':{'code':'origin_denied','message':'Request origin is not allowed'}},status_code=403)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def error(code, message, status):
        return JSONResponse({'error':{'code':code,'message':message}},status_code=status)

    @app.exception_handler(PlannerError)
    async def planner_error(_, exc):
        return error(exc.code,exc.message,exc.status)

    @app.exception_handler(StaleState)
    async def stale(_, exc):
        return error('stale_state',str(exc),409)

    @app.exception_handler(ValueError)
    async def invalid(_, exc):
        return error('invalid_action',str(exc),400)

    @app.exception_handler(HTTPException)
    async def http_error(_, exc):
        return error('request_failed',str(exc.detail),exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def invalid_input(_, exc):
        return error('invalid_request','Invalid request fields: '+', '.join('.'.join(map(str,e['loc'])) for e in exc.errors()),422)

    @app.exception_handler(sqlite3.OperationalError)
    async def unavailable(_, exc):
        return error('storage_unavailable','Storage is busy. Refresh and try again.',503)

    def selected(request: Request):
        token = request.cookies.get('banking_session')
        s = sessions.get(token)
        if s is None or s.expires < time.time():
            if token:
                sessions.pop(token,None)
            raise HTTPException(401,'Select a mock customer to continue')
        return s

    def scope_check(s, request):
        if request.headers.get('X-Context-Scope') != s.scope:
            raise StaleState('Customer or scenario changed. Refresh before making changes.')

    def bind(s):
        if s.scenario == 'customer':
            uid = s.customer
            def provider(customer):
                if customer != uid:
                    raise ValueError('Customer scope mismatch')
                profile=profiles[uid]
                return {'txns':ledger[uid], 'as_of':date(2026,11,15),
                        'accounts':tuple(AccountSnapshot(a['account_id'],a['type'],a['balance'],date(2026,11,15))
                                         for a in profile['accounts'])}
        else:
            uid = s.sandbox
            mode = s.scenario
            def provider(customer):
                if customer != uid:
                    raise ValueError('Sandbox scope mismatch')
                txns=[replace(t,user_id=uid) for t in demo_transactions(mode=='vet_paid')]
                raw={'txns':txns,'as_of':AS_OF,
                     'accounts':(AccountSnapshot('demo-everyday','transaction',2000,date(2026,1,1)),
                                 AccountSnapshot('demo-savings','savings',9300,AS_OF,8100,1200))}
                if mode=='vet_paid':
                    raw['event_evidence']=(PostedEventEvidence(txns[-1].transaction_id,AS_OF,'emergency_veterinary'),)
                if mode=='vet_unpaid':
                    raw['pending_expenses']=(PendingExpense('vet',1850,'Pets',AS_OF,AS_OF),)
                return raw
            revision,_=store.read(uid,AS_OF)
            if revision==0:
                def seed(state):
                    state['protected_categories']=['Health','Pets']
                    state['goals']['demo-holiday']={'goal_id':'demo-holiday','kind':'holiday','target':6000,
                        'allocated':1200,'deadline':'2027-05-31','version':1}
                store.update(uid,as_of=AS_OF,expected_revision=0,mutate=seed)
        return uid, BankingService(store,provider)  # No validator configured: AI proposals fail closed.

    def transactions(service, uid):
        raw=service._inputs(uid)
        _,state=store.read(uid,raw['as_of'])
        rows=[]
        for t in sorted((t for t in raw['txns'] if t.date<=raw['as_of']),key=lambda t:t.date,reverse=True):
            c=classify_transaction(t,merchant_corrections=state['corrections'],
                                   explicit_correction=state['transaction_corrections'].get(t.transaction_id))
            rows.append({'transaction_id':t.transaction_id,'date':t.date.isoformat(),'description':t.description,
                         'amount':t.amount,'type':t.type,'category':c.category,
                         'essential':c.essential,'classification_source':c.source,'confidence':c.confidence,'uncertain':c.uncertain})
        return rows

    def goal_rows(goals, as_of):
        result=[]
        for g in goals:
            deadline=date.fromisoformat(g['deadline'])
            months=max(0,(deadline.year-as_of.year)*12+deadline.month-as_of.month)
            arithmetic=required_contribution(target=g['target'],allocated=g['allocated'],months=months)
            result.append({**g,'progress_percent':round(min(100,g['allocated']/g['target']*100),2) if g['target'] else 100,
                           'contribution_periods':months,'required_monthly':arithmetic['monthly_amount']})
        return result

    def overview(s):
        uid,service=bind(s)
        view=service.banking_view(uid)
        context=service.planning_context(uid)
        raw=service._inputs(uid)
        corrected,_=service._assemble(raw,store.read(uid,raw['as_of'])[1])
        flows=monthly_flows(corrected,complete_months_only=False)
        month=next((f for f in flows if f.month==raw['as_of'].strftime('%Y-%m')),None)
        profile=profiles[s.customer] if s.scenario=='customer' else {'first_name':'Holiday demo','persona':'Isolated synthetic veterinary scenario','property_value':0}
        account_names={a['account_id']:a['name'] for a in profiles[s.customer]['accounts']} if s.scenario=='customer' else {'demo-everyday':'Westpac Choice','demo-savings':'Westpac eSaver'}
        balances=view['insights']['balances']
        accounts=[{'id':a['account_id'],'name':account_names.get(a['account_id'],'Account'),
                   'kind':'loan' if a['kind']=='loan' else 'cash','balance':a['balance'],
                   'protected':a['protected'],'goal_available':a['goal_available']} for a in balances]
        recent=transactions(service,uid)[:20]
        view['insights']['cashflow'] = context['financial_insights']['cashflow']
        return {'scope_version':s.scope,'scenario':s.scenario,'selected_customer':s.customer,
                'revision':view['revision'],'state_version':context['state_version'],'as_of':raw['as_of'].isoformat(),
                'firstName':profile['first_name'],'persona':profile['persona'],'accounts':accounts,
                'cashFlow':{'since':raw['as_of'].replace(day=1).isoformat(),'income':month.income if month else 0,
                            'expenses':round(month.essential+month.discretionary,2) if month else 0,
                            'net':round(month.net,2) if month else 0},
                'netWorth':{'assets':round(sum(max(0,a['balance'] or 0) for a in accounts)+profile.get('property_value',0),2),
                            'liabilities':round(-sum(min(0,a['balance'] or 0) for a in accounts),2),
                            'net':round(sum(a['balance'] or 0 for a in accounts)+profile.get('property_value',0),2)},
                'categoriesTotal':round(month.essential+month.discretionary,2) if month else 0,
                'recent':recent,'financial':{k:view['insights'][k] for k in
                    ('observation','income','spending','cashflow','budgets','bills_calendar','savings_finder','personalised_insights','uncertainty')},
                'shocks':[{k:v for k,v in e.items() if not k.startswith('_') and k!='transaction_ids'} for e in view['insights']['shocks']],
                'shock_history':service.shock_history(uid),'goals':goal_rows(view['goals'],raw['as_of']),
                'proposals':view['proposals'],'goal_history':service.goal_history(uid),
                'subscriptions':view['subscriptions'],'categories':sorted(CATEGORIES),
                'goal_available':round(sum(a['goal_available'] for a in balances),2),
                'ai_status':'Not connected; AI proposals require an independent validator.'}

    @app.get('/api/health')
    def health():
        return {'status':'ok','data':'synthetic','authentication':'mock_session'}

    @app.get('/api/customers')
    def customers():
        return {'customers':[{'id':uid,'firstName':p['first_name'],'persona':p['persona']} for uid,p in profiles.items()]}

    @app.post('/api/session')
    def login(body:SessionInput,request:Request,response:Response):
        if body.demo_acknowledged is not True or body.customer_id not in profiles:
            raise HTTPException(400,'Choose a listed synthetic customer and acknowledge demo mode')
        old=request.cookies.get('banking_session');sessions.pop(old,None)
        token=secrets.token_urlsafe(32)
        sessions[token]=Session(body.customer_id,time.time()+12*3600)
        response.set_cookie('banking_session',token,httponly=True,samesite='strict',max_age=12*3600,path='/api')
        return {'selected_customer':body.customer_id}

    @app.delete('/api/session')
    def logout(request:Request,response:Response):
        sessions.pop(request.cookies.get('banking_session'),None)
        response.delete_cookie('banking_session',path='/api')
        return {'signed_out':True}

    @app.get('/api/overview')
    def get_overview(s=Depends(selected)):
        with s.lock:
            return overview(s)

    @app.post('/api/scenario')
    def scenario(body:ScenarioInput,request:Request,s=Depends(selected)):
        with s.lock:
            scope_check(s,request)
            if body.reset:
                s.sandbox='sandbox-'+uuid4().hex
            s.scenario=body.scenario
            s.scope=uuid4().hex
            return overview(s)

    @app.get('/api/transactions')
    def get_transactions(limit:Annotated[int,Query(ge=1,le=1000)]=100, s=Depends(selected)):
        with s.lock:
            uid,service=bind(s)
            return {'transactions':transactions(service,uid)[:limit],'scope_version':s.scope}

    @app.get('/api/planning-context')
    def planning_context(s=Depends(selected)):
        with s.lock:
            uid,service=bind(s)
            return service.planning_context(uid)

    def read_field(s,field):
        with s.lock:
            data=overview(s)
            return {'data':data['financial'].get(field,data.get(field)), 'revision':data['revision'],'scope_version':s.scope}

    # Named resource endpoints share the same existing backend calculations.
    for path,key in [('budgets','budgets'),('bills','bills_calendar'),('opportunities','savings_finder'),
                     ('insights','personalised_insights'),('subscriptions','subscriptions'),('goals','goals'),('shocks','shocks')]:
        def make_endpoint(key):
            def endpoint(s=Depends(selected)):
                return read_field(s,key)
            return endpoint
        app.add_api_route('/api/'+path,make_endpoint(key),methods=['GET'],name='get_'+path)

    def mutate(s,request,body,operation):
        with s.lock:
            scope_check(s,request)
            uid,service=bind(s)
            operation(uid,service)
            return overview(s)

    @app.put('/api/budgets')
    def budget(body:BudgetInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.set_budget(uid,BudgetLimit(body.category,body.monthly_limit,body.protected),confirmed=body.confirmed,expected_revision=body.expected_revision))

    @app.patch('/api/transactions/{transaction_id}/category')
    def correct(transaction_id:str,body:CorrectionInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.correct_category(uid,transaction_id,body.category,body.essential,confirmed=body.confirmed,expected_revision=body.expected_revision))

    @app.post('/api/feedback')
    def feedback(body:FeedbackInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.record_feedback(uid,**body.model_dump()))

    @app.post('/api/goals')
    def create_goal(body:GoalInput,request:Request,s=Depends(selected)):
        goal=Goal(uuid4().hex,body.kind,body.target,body.allocated,body.deadline)
        return mutate(s,request,body,lambda uid,svc:svc.create_goal(uid,goal,confirmed=body.confirmed,expected_revision=body.expected_revision))

    @app.post('/api/goals/{goal_id}/proposals')
    def propose(goal_id:str,body:ProposalInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.propose_goal_change(uid,goal_id,body.changes.model_dump(exclude_none=True,mode='json'),expected_revision=body.expected_revision,source=body.source))

    @app.post('/api/proposals/{proposal_id}/decision')
    def decide(proposal_id:str,body:DecisionInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.decide_proposal(uid,proposal_id,body.decision,confirmed=body.confirmed,expected_revision=body.expected_revision))

    @app.post('/api/subscriptions/{subscription_id}/action')
    def subscription_action(subscription_id:str,body:SubscriptionInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.subscription_action(uid,subscription_id,body.action,confirmed=body.confirmed,expected_revision=body.expected_revision))

    @app.post('/api/shocks/{event_ref}/action')
    def shock_action(event_ref:str,body:ShockActionInput,request:Request,s=Depends(selected)):
        return mutate(s,request,body,lambda uid,svc:svc.record_shock_action(uid,event_ref,**body.model_dump()))

    @app.post('/api/search')
    def search(body:SearchInput,s=Depends(selected)):
        with s.lock:
            uid,svc=bind(s)
            return svc.search(uid,body.question)

    @app.get('/api/planner/status')
    def planner_status(s=Depends(selected)):
        return planner.status()

    @app.post('/api/planner/assess')
    def planner_assess(body:PlannerInput,request:Request,s=Depends(selected)):
        with s.lock:
            scope_check(s,request)
            uid,svc=bind(s)
            scope=s.scope
        # Network calls run outside the session lock; changed scope invalidates their result.
        authorised=lambda:s.scope==scope and s.expires>=time.time() and sessions.get(request.cookies.get('banking_session')) is s
        values=body.model_dump(exclude={'background'})
        if body.background:
            return JSONResponse(planner.start(svc,uid,**values,still_authorised=authorised),status_code=202)
        return planner.generate(svc,uid,**values,still_authorised=authorised)

    @app.get('/api/planner/requests/{request_id}')
    def planner_request(request_id:str,request:Request,s=Depends(selected)):
        with s.lock:
            scope_check(s,request);uid,svc=bind(s)
        return planner.request_status(svc,uid,request_id)

    @app.post('/api/planner/requests/{request_id}/cancel')
    def planner_cancel(request_id:str,request:Request,s=Depends(selected)):
        with s.lock:
            scope_check(s,request);uid,_=bind(s)
        return planner.cancel(uid,request_id)

    @app.post('/api/planner/requests/{request_id}/retry')
    def planner_retry(request_id:str,body:PlannerRetryInput,request:Request,s=Depends(selected)):
        with s.lock:
            scope_check(s,request);uid,svc=bind(s);scope=s.scope
        authorised=lambda:s.scope==scope and s.expires>=time.time() and sessions.get(request.cookies.get('banking_session')) is s
        return JSONResponse(planner.retry(svc,uid,request_id,expected_revision=body.expected_revision,still_authorised=authorised),status_code=202)

    @app.get('/api/planner/sessions')
    def planner_sessions(s=Depends(selected)):
        with s.lock:
            uid,svc=bind(s)
            return planner.history(svc,uid)

    @app.get('/api/planner/sessions/{session_id}')
    def planner_session(session_id:str,s=Depends(selected)):
        with s.lock:
            uid,svc=bind(s)
            return planner.history(svc,uid,session_id)

    @app.get('/api/planner/drafts/{draft_id}')
    def planner_draft(draft_id:str,s=Depends(selected)):
        with s.lock:
            uid,svc=bind(s)
            return planner.draft(svc,uid,draft_id)

    return app


app=create_app()
