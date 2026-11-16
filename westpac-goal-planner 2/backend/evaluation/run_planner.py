"""Optional live quality/smoke run. Default writes fixture expectations without API calls."""
import argparse
import json
from pathlib import Path
import time
from planning.agent import GeminiPlanner
from planning.gemini import GeminiTransport,PlannerError
from .planner_scenarios import cases,review


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--live',action='store_true')
    p.add_argument('--cases',nargs='+',default=['normal','vet_paid','vet_unpaid'])
    p.add_argument('--output',default='examples/planner')
    p.add_argument('--interval',type=float,default=30)
    args=p.parse_args()
    if len(args.cases)>4: p.error('At most four live cases per run; keep free-tier usage bounded.')
    target=Path(args.output);target.mkdir(parents=True,exist_ok=True)
    all_cases=cases()
    (target/'expectations.json').write_text(json.dumps([{k:v for k,v in c.items() if k!='context'} for c in all_cases],indent=2)+'\n')
    if not args.live:
        print('Wrote authored quality expectations. No Gemini API calls were made.');return
    selected=[c for c in all_cases if c['name'] in args.cases]
    if len(selected)!=len(set(args.cases)):p.error('Unknown quality case')
    transport=GeminiTransport();results=[]
    for index,case in enumerate(selected):
        if index and args.interval>0: time.sleep(min(60,args.interval))
        agent=GeminiPlanner(transport)
        try:
            draft=agent.run(case['context'],goal_ref='goal-1',question=case['question'])
            (target/(case['name']+'.json')).write_text(json.dumps(draft,indent=2,allow_nan=False)+'\n')
            result={**review(case,draft),'live':True,'generated':True,'metadata':draft['metadata']}
        except PlannerError as exc:
            result={'scenario':case['name'],'live':True,'generated':False,'error':{'code':exc.code,'message':exc.message},'metadata':agent.last_metrics}
        results.append(result)
        print(json.dumps(result),flush=True)
        (target/'live-results.json').write_text(json.dumps(results,indent=2,allow_nan=False)+'\n')

if __name__=='__main__': main()
