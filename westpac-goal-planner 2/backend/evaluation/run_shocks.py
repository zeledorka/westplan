"""python -m evaluation.run_shocks [output_directory]"""
from pathlib import Path
import json
import sys
from finstats.shock_context import normalized_shocks, expectedness_for
from planning.context import build_planning_context, context_json
from .shock_scenarios import scenarios


def evaluate():
    results=[]
    for c in scenarios():
        events=normalized_shocks(c['txns'],as_of=c['as_of'],**c['kwargs'])
        matches=[e for e in events if (set(e['transaction_ids'])&set(c['target_ids'])) or e['event_type'] in c['target_types']]
        predicted=bool(matches)
        expectation=matches[0]['expectedness'] if matches else None
        if c['expectedness'] and not matches and c['target_ids']:
            t=next((t for t in c['txns'] if t.transaction_id in c['target_ids']),None)
            if t and t.is_expense:
                expectation=expectedness_for(t,[r for r in c['txns'] if r.date<t.date],**{
                    k:v for k,v in c['kwargs'].items() if k in {'planned_expenses','pending_expenses'}})[0]
        results.append({'scenario':c['name'],'reason':c['reason'],'label_positive':c['positive'],'predicted_positive':predicted,
                        'expectedness_label':c['expectedness'],'expectedness_observed':expectation,
                        'target_events':[{k:e[k] for k in ('event_ref','event_type','event_amount','detectors','confidence','potential_disruption','expectedness')} for e in matches],
                        'other_event_count':len(events)-len(matches)})
    tp=sum(r['label_positive'] and r['predicted_positive'] for r in results)
    fp=sum(not r['label_positive'] and r['predicted_positive'] for r in results)
    fn=sum(r['label_positive'] and not r['predicted_positive'] for r in results)
    tn=sum(not r['label_positive'] and not r['predicted_positive'] for r in results)
    return {'scope':'Target-level review-worthy activity on 14 authored synthetic scenarios; unlabelled incidental events are reported separately.',
            'not_real_world_accuracy':True,'true_positives':tp,'false_positives':fp,'false_negatives':fn,'true_negatives':tn,
            'precision':tp/(tp+fp) if tp+fp else None,'recall':tp/(tp+fn) if tp+fn else None,'scenarios':results}


def main():
    target=Path(sys.argv[1] if len(sys.argv)>1 else 'examples/shock-evaluation')
    target.mkdir(parents=True,exist_ok=True)
    report=evaluate()
    (target/'results.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    cases={c['name']:c for c in scenarios()}
    for name in ('emergency_vet','missing_salary'):
        c=cases[name]
        (target/(name+'.json')).write_text(json.dumps(normalized_shocks(c['txns'],as_of=c['as_of'],**c['kwargs']),indent=2,allow_nan=False)+'\n')
        (target/(name+'_planning_context.json')).write_text(context_json(build_planning_context(c['txns'],as_of=c['as_of'],**c['kwargs']))+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='scenarios'},indent=2))


if __name__=='__main__':
    main()
