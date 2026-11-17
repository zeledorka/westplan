"""Customer-bound Gemini orchestration. This service has no goal mutation capability."""
from hashlib import sha256
import json
import logging
from threading import BoundedSemaphore, Lock, Thread
import time
from uuid import uuid4
from .agent import GeminiPlanner, private_text
from .gemini import PlannerError, GenerationRetryPolicy, execution_deadline, bounded_setting
from .proposal_validation import correction_limit
from .checkpoint import unpack
from .planner_store import PlannerStore
from .safety import assess_draft, POLICY_VERSION
from .safety_revision import safety_revision_limit
from .store import StaleState
from finstats.contracts import CATEGORIES

log=logging.getLogger('planning.gemini')

class PlannerService:
    def __init__(self, store, transport, *, cooldown_seconds=10):
        self.store=PlannerStore(store);self.transport=transport
        self.gate=BoundedSemaphore(1);self.cooldown=cooldown_seconds
        self.jobs={};self.jobs_lock=Lock()

    def status(self):
        policy=GenerationRetryPolicy.from_environment()
        return {'available':self.transport.available,'model':self.transport.model,'provider':getattr(self.transport,'provider','test_transport'),'minimum_request_interval_seconds':bounded_setting('PLANNER_MIN_REQUEST_INTERVAL_SECONDS',5,0,60),'draft_only':True,
                'independent_checker_available':True,'safety_policy_version':POLICY_VERSION,'automatic_safety_revision':safety_revision_limit()>0,'maximum_safety_revisions':safety_revision_limit(),'ai_activation_enabled':False,
                'maximum_model_calls':bounded_setting('GEMINI_MAX_LOGICAL_GENERATIONS',11,2,12),'maximum_tool_calls':8,
                'maximum_generation_attempts':policy.max_attempts,'maximum_retries_per_call':policy.max_retries,
                'execution_deadline_seconds':execution_deadline(),'maximum_session_generation_attempts':bounded_setting('GEMINI_MAX_SESSION_ATTEMPTS',40,1,96),'maximum_explicit_resumes':bounded_setting('GEMINI_MAX_RESUMES',2,0,3),'maximum_correction_attempts':correction_limit()}

    def generate(self, bank, customer, *, goal_id, question, request_id, expected_revision,
                 session_id=None, protected_categories=(), still_authorised=lambda:True,_run_id=None):
        input_session_id=session_id
        deadline=time.monotonic()+execution_deadline()
        if not question.strip() or len(question)>1600 or not request_id or len(request_id)>80:
            raise ValueError('Supply a question (at most 1600 characters) and a request ID')
        if not set(protected_categories)<=CATEGORIES:
            raise ValueError('Unknown temporary protected category')
        if not still_authorised():raise StaleState('Customer or scenario scope changed before planning.')
        raw=bank._inputs(customer)
        revision,state=bank.store.read(customer,raw['as_of'])
        if revision!=expected_revision: raise StaleState('Financial state changed. Refresh before planning.')
        if goal_id not in state['goals']: raise ValueError('Unknown selected goal')
        context=bank.planning_context(customer)
        question=private_text(question)
        intent=sha256(json.dumps([goal_id,question,session_id,sorted(protected_categories)]).encode()).hexdigest()
        previous=self.store.request(customer,request_id)
        claimed=bool(previous and _run_id and previous.get('run_id')==_run_id and previous['status'] in {'queued','running'})
        if previous and not claimed:
            if previous['intent']!=intent: raise StaleState('Request ID already belongs to a different planning request')
            if previous.get('financial_version') is not None and previous['financial_version']!=context['state_version']: raise StaleState('Cached draft is stale. Use a new request ID after review.')
            if previous['status']!='draft_unverified':
                raise PlannerError(**previous.get('error',{'code':'planner_busy','message':'Request is still running. Check its status.','status':409}))
            return previous['draft']
        if claimed and previous.get('financial_version') and previous['financial_version']!=context['state_version']:
            self.store.alter(customer,request_id,lambda r:{**r,'status':'stale','checkpoint':None,'error':{'code':'stale_state','message':'Financial facts or goal versions changed. Start a new assessment.','status':409}})
            raise StaleState('Financial facts or goal versions changed. Start a new assessment.')
        if claimed and (previous.get('model') and previous['model']!=self.transport.model or previous.get('provider',getattr(self.transport,'provider',None))!=getattr(self.transport,'provider',None)):
            raise PlannerError('planner_configuration','The configured model changed; start a fresh assessment.',409)
        history=[r for r in self.store.history(customer,session_id) if r['request_id']!=request_id] if session_id else []
        if session_id and (not history or any(r['goal_id']!=goal_id for r in history)):
            raise ValueError('Unknown planning session for this goal and customer')
        if len(history)>=6: raise PlannerError('planner_limit','This session reached six turns. Start a new assessment.',429)
        if not self.transport.available: raise PlannerError('planner_unconfigured','The selected model provider is not configured on the backend. Banking and manual goals remain available.',503)
        now=time.time()
        if self.store.count_recent(customer,now-3600,request_id)>=12 or self.cooldown and self.store.count_recent(customer,now-self.cooldown,request_id):
            raise PlannerError('planner_rate_limited','Please wait before requesting more planning. This demo allows twelve turns per hour.',429)
        if not self.gate.acquire(blocking=False):
            raise PlannerError('planner_busy','Another planning request is running. Please wait and try again.',429)
        session_id=previous['session_id'] if claimed else session_id or uuid4().hex
        ref=context['goals'][list(state['goals']).index(goal_id)]['goal_ref']
        recent=[]
        for row in history[-3:]:
            if row['status']!='draft_unverified': continue
            draft=row['draft']
            recent.append({'question':row['question'],'answer':draft['result']['answer'][:1200],
                'independent_safety_feedback':draft.get('safety_assessment',{}).get('revision_feedback'),
                'strategies':[{'title':option['title'],'rationale':option['rationale'][:800],
                    'plans':[{k:plan[k] for k in ('goal_ref','contribution','frequency','pause_months','target','deadline','completion_date')}
                             for plan in draft['calculations'][option['calculation_ref']]['plans']]}
                              for option in draft['result']['strategies']],
                'historical_only':row['financial_version']!=context['state_version']})
        aliases={gid:context['goals'][i]['goal_ref'] for i,gid in enumerate(state['goals'])}
        approved=[{'goal_ref':aliases[g['goal_id']],'version':g['version'],'target':g['target'],
                   'allocated':g['allocated'],'deadline':g['deadline']} for g in bank.goal_history(customer)[-12:] if g['goal_id'] in aliases]
        supplement={'temporary_protected_categories':list(protected_categories),'approved_goal_history':approved,
                    'current_schedules_and_priorities':context.get('recorded_goal_schedules') or 'Not recorded by the existing app; ask rather than invent a current contribution or priority.',
                    'hypotheticals_do_not_change_the_actual_plan':True}
        record={**(previous if claimed else {}),'session_id':session_id,'request_id':request_id,'goal_id':goal_id,'intent':intent,
                'created_at':now,'question':question,'financial_version':context['state_version']}
        record['request_input']={'goal_id':goal_id,'question':question,'request_id':request_id,'expected_revision':expected_revision,'session_id':input_session_id,'protected_categories':list(protected_categories)}
        record.update(status='running',run_id=_run_id or uuid4().hex,model=self.transport.model,provider=getattr(self.transport,'provider','test_transport'),
                      deadline_at=time.time()+max(0,deadline-time.monotonic()),lease_until=time.time()+45)
        run_id=record['run_id']
        # Claim synchronous requests too, so duplicate requests cannot repeat tool work.
        if not claimed:
            def claim(old):
                if old:raise PlannerError('planner_busy','This request already has an execution. Check its status.',409)
                return record
            try:self.store.alter(customer,request_id,claim)
            except Exception:self.gate.release();raise
        else:
            try:self.store.save(customer,record)
            except Exception:self.gate.release();raise
        def cancelled():
            if bank.store.read(customer,raw['as_of'])[0]!=revision:
                raise StaleState('Financial or goal revision changed during planning.')
            current=self.store.request(customer,request_id)
            return bool(not current or current.get('run_id')!=run_id or current.get('cancel_requested') or not still_authorised())
        def checkpoint(saved):
            def update(current):
                if not current or current.get('run_id')!=run_id:raise PlannerError('planner_cancelled','Execution ownership changed.',409)
                return {**current,'checkpoint':saved,'lease_until':time.time()+45}
            self.store.alter(customer,request_id,update)
        def progress(value):
            now=time.time();safe={**value,'updated_at':now}
            if value['status']=='waiting':safe['next_retry_at']=now+value['delay_seconds']
            def update(current):
                if current.get('run_id')!=run_id:raise PlannerError('planner_cancelled','Execution ownership changed.',409)
                return {**current,'progress':safe,'lease_until':now+max(30,value.get('delay_seconds',0))+40}
            self.store.alter(customer,request_id,update)
        agent=None
        try:
            agent=GeminiPlanner(self.transport,timeout_seconds=max(0,deadline-time.monotonic()))
            def checker(candidate):
                return assess_draft(candidate,context,candidate['baseline_calculations'],expected_goal_ref=ref)
            draft=agent.run(context,goal_ref=ref,question=question,history=recent,supplement=supplement,checkpoint=previous.get('checkpoint') if claimed else None,on_checkpoint=checkpoint,on_progress=progress,cancelled=cancelled,safety_checker=checker)
            draft['activation_allowed']=False  # PASS does not bypass the existing approval guard.
            draft['metadata']['safety_status']=draft['safety_assessment']['status']
            # Recheck both authorised UI scope and actual context after the network call.
            if not still_authorised() or bank.planning_context(customer)['state_version']!=context['state_version']:
                raise StaleState('Customer, scenario or financial facts changed during planning. Reassess with current information.')
            if time.monotonic()>=deadline:
                raise PlannerError('planner_limit','Planner execution deadline expired. No goals changed.',504)
            draft.update(session_id=session_id,request_id=request_id,goal_id=goal_id,goal_version=state['goals'][goal_id]['version'],
                         goal_versions={k:v['version'] for k,v in state['goals'].items()})
            if cancelled():raise PlannerError('planner_cancelled','Planning cancelled. Your goals have not changed.',409)
            record.pop('safety_revision',None);record.pop('error',None)
            record.update(status='draft_unverified',draft=draft,checkpoint=None,progress={'status':'completed'})
            self.store.save(customer,record)
            return draft
        except StaleState:
            if agent and agent.last_safety_revision:record['safety_revision']=agent.last_safety_revision
            record.pop('draft',None);record.update(status='stale',checkpoint=None,progress={'status':'stale'},error={'code':'stale_state','message':'Financial context or authorisation changed. Start a fresh assessment.','status':409})
            self.store.save(customer,record)
            raise
        except PlannerError as exc:
            if agent and agent.last_safety_revision:record['safety_revision']=agent.last_safety_revision
            current=self.store.request(customer,request_id) or record
            cp=getattr(agent,'last_checkpoint',None) if agent else current.get('checkpoint')
            interrupted=bool(cp and (exc.resume_allowed or exc.code=='planner_limit'))
            if cp:
                saved=unpack(cp['state'])
                if not saved['pending'] and not (saved['ready'] and exc.code=='planner_limit' and agent.last_metrics['final_outcome']=='budget_exhausted'):interrupted=False
                if saved['pending'] and saved['metrics']['model_calls']>=bounded_setting('GEMINI_MAX_SESSION_ATTEMPTS',40,1,96):interrupted=False
                if agent and saved['metrics']['tool_calls']>agent.max_tool_calls:interrupted=False
            record={**record,'checkpoint':cp if interrupted else None}
            record.pop('draft',None)
            status='cancelled' if exc.code=='planner_cancelled' else 'interrupted' if interrupted else 'failed'
            error={'code':exc.code,'message':exc.message,'status':exc.status,'upstream_status':exc.upstream_status,
                   'retry_after_seconds':exc.retry_after_seconds,'retryable':exc.retryable,'resume_allowed':exc.resume_allowed,'provider_details':exc.provider_details}
            reason='deadline_reached' if time.monotonic()>=deadline else 'provider_wait_exceeds_deadline' if exc.code=='planner_limit' and exc.retry_after_seconds else 'attempt_budget_exhausted' if exc.code=='planner_limit' else 'provider_unavailable' if interrupted else 'validation_or_permanent_failure'
            record.update(status=status,error=error,progress={'status':status,'reason':reason},
                          retry_not_before=time.time()+(exc.retry_after_seconds or (60 if exc.upstream_status==429 else 0)),
                          metadata={**(agent.last_metrics if agent else {}),'success':False,'error_code':exc.code})
            self.store.save(customer,record)
            raise
        finally:
            metrics={**(agent.last_metrics if agent else {}),'success':record.get('status')=='draft_unverified',
                     'error_code':record.get('error',{}).get('code')}
            if record.get('status') not in {'draft_unverified','failed','interrupted','cancelled','stale'}:
                metrics['final_outcome']='context_or_scope_rejected'
            log.info('planner_request %s',json.dumps(metrics,allow_nan=False))
            self.gate.release()

    def history(self, bank, customer, session_id=None):
        context=bank.planning_context(customer)
        records=self.store.history(customer,session_id)
        return {'sessions':[{'session_id':r['session_id'],'request_id':r['request_id'],'goal_id':r['goal_id'],
                'question':r['question'],'status':r['status'],'draft':r.get('draft'),
                'error':r.get('error'),'safety_revision':r.get('safety_revision'),'progress':r.get('progress',{}),'resumable':r['status']=='interrupted' and bool(r.get('checkpoint')),'stale':r['financial_version']!=context['state_version']} for r in records],
                'financial_version':context['state_version']}

    def draft(self, bank, customer, draft_id):
        result=self.history(bank,customer)
        row=next((r for r in result['sessions'] if r.get('draft') and r['draft']['draft_id']==draft_id),None)
        if row is None: raise ValueError('Unknown customer draft')
        return {**row['draft'],'stale':row['stale']}

    def request_status(self, bank, customer, request_id):
        record=self.store.request(customer,request_id)
        if not record:raise ValueError('Unknown customer planning request')
        if record['status'] in {'queued','running'} and record.get('lease_until',0)<time.time():
            record=self.store.alter(customer,request_id,lambda r:{**r,'status':'interrupted','progress':{'status':'interrupted'},
                'error':{'code':'planner_interrupted','message':'Execution stopped or expired. Retry explicitly to resume.','status':503}} if r['status'] in {'queued','running'} and r.get('lease_until',0)<time.time() else r)
        current_revision,_=bank.store.read(customer,bank._inputs(customer)['as_of'])
        stale=current_revision!=record.get('request_input',{}).get('expected_revision',current_revision)
        if record['status'] not in {'queued','running'} and record.get('financial_version') is not None:
            stale=record['financial_version']!=bank.planning_context(customer)['state_version']
        if stale and record['status']=='interrupted':
            record=self.store.alter(customer,request_id,lambda r:{**r,'status':'stale','checkpoint':None,'progress':{'status':'stale'}} if r['status']=='interrupted' else r)
        public={'request_id':request_id,'session_id':record['session_id'],'goal_id':record['goal_id'],
                'status':'completed' if record['status']=='draft_unverified' else record['status'],
                'progress':record.get('progress',{}),'deadline_at':record.get('deadline_at'),
                'error':record.get('error'),'retry_not_before':record.get('retry_not_before'),'stale':stale,'resumable':record['status']=='interrupted' and bool(record.get('checkpoint')) and not stale and record.get('resume_count',0)<bounded_setting('GEMINI_MAX_RESUMES',2,0,3),
                'safety_revision':record.get('safety_revision'),
                'cancel_requested':bool(record.get('cancel_requested')),'poll_after_ms':5000 if record.get('progress',{}).get('status')=='waiting' else 2500}
        if record.get('draft'):public['draft']={**record['draft'],'stale':stale}
        return public

    def start(self, bank, customer, *, still_authorised=lambda:True, resume=False, **body):
        """Explicitly requested, bounded in-process job; durable state remains in SQLite."""
        request_id=body['request_id']
        raw=bank._inputs(customer);revision,state=bank.store.read(customer,raw['as_of'])
        if body['expected_revision']!=revision:raise StaleState('Financial state changed. Refresh before planning.')
        if body['goal_id'] not in state['goals']:raise ValueError('Unknown selected goal')
        if not body['question'].strip() or len(body['question'])>1600 or not request_id or len(request_id)>80:raise ValueError('Invalid planning request')
        if not self.transport.available:raise PlannerError('planner_unconfigured','The selected model provider is not configured.',503)
        if not set(body.get('protected_categories',()))<=CATEGORIES:raise ValueError('Unknown temporary protected category')
        body={**body,'question':private_text(body['question'])}
        intent=sha256(json.dumps([body['goal_id'],body['question'],body.get('session_id'),sorted(body.get('protected_categories',()))]).encode()).hexdigest()
        with self.jobs_lock:
            self.jobs={key:thread for key,thread in self.jobs.items() if thread.is_alive()}
            existing=self.store.request(customer,request_id)
            if existing and existing['intent']!=intent:raise StaleState('Request ID already belongs to another planning request')
            if existing and not resume:return self.request_status(bank,customer,request_id)
            if any(t.is_alive() for t in self.jobs.values()):raise PlannerError('planner_busy','Another planning request is running. Please wait.',429)
            token=uuid4().hex;now=time.time()
            def claim(old):
                if old:
                    expired=old['status'] in {'queued','running'} and old.get('lease_until',0)<now
                    if not resume or old['status']!='interrupted' and not expired:raise PlannerError('planner_busy','This request cannot be resumed in its current state.',409)
                    if old.get('retry_not_before',0)>now:raise PlannerError('planner_rate_limited','Provider Retry-After has not elapsed yet.',429)
                    if old.get('resume_count',0)>=bounded_setting('GEMINI_MAX_RESUMES',2,0,3):raise PlannerError('planner_limit','This request reached its explicit resume limit. Start a new assessment.',429)
                    return {**old,'status':'queued','run_id':token,'cancel_requested':False,'resume_count':old.get('resume_count',0)+1,
                            'lease_until':now+execution_deadline()+45,'progress':{'status':'queued'}}
                if resume:raise ValueError('Unknown interrupted request')
                return {'request_id':request_id,'session_id':body.get('session_id') or uuid4().hex,'goal_id':body['goal_id'],
                        'question':body['question'],'request_input':body,'intent':intent,'created_at':now,'financial_version':None,
                        'status':'queued','run_id':token,'resume_count':0,'lease_until':now+execution_deadline()+45,'progress':{'status':'queued'}}
            self.store.alter(customer,request_id,claim)
            def work():
                try:self.generate(bank,customer,**body,still_authorised=still_authorised,_run_id=token)
                except Exception as exc:
                    # Known failures are already stored by generate. Preflight failures are terminal.
                    def fail(current):
                        if current.get('run_id')!=token or current['status'] not in {'queued','running'}:return current
                        temporary=bool(current.get('checkpoint') and getattr(exc,'code',None) in {'planner_busy','planner_rate_limited'})
                        terminal='stale' if isinstance(exc,StaleState) else 'interrupted' if temporary else 'failed'
                        return {**current,'status':terminal,'checkpoint':current.get('checkpoint') if temporary else None,
                                'progress':{'status':terminal},'error':{'code':'stale_state' if isinstance(exc,StaleState) else getattr(exc,'code','planner_failed'),
                                'message':getattr(exc,'message','Planning stopped. Refresh financial facts before retrying.'),'status':getattr(exc,'status',409)}}
                    self.store.alter(customer,request_id,fail)
            thread=Thread(target=work,name='gemini-planner',daemon=True);self.jobs[(customer,request_id)]=thread;thread.start()
        return {'request_id':request_id,'session_id':self.store.request(customer,request_id)['session_id'],'status':'queued','poll_after_ms':1000}

    def retry(self, bank, customer, request_id, *, expected_revision, still_authorised=lambda:True):
        record=self.store.request(customer,request_id)
        if not record or not record.get('request_input'):raise ValueError('Unknown resumable customer request')
        return self.start(bank,customer,**{**record['request_input'],'expected_revision':expected_revision},resume=True,still_authorised=still_authorised)

    def cancel(self, customer, request_id):
        def cancel(record):
            if not record:raise ValueError('Unknown customer planning request')
            if record['status'] in {'queued','running'}:return {**record,'cancel_requested':True,'progress':{'status':'cancelling'}}
            if record['status']=='interrupted':return {**record,'status':'cancelled','checkpoint':None,'cancel_requested':True,'progress':{'status':'cancelled'}}
            return record
        record=self.store.alter(customer,request_id,cancel)
        return {'request_id':request_id,'status':'cancelling' if record.get('cancel_requested') and record['status'] in {'queued','running'} else record['status']}
