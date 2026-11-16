"""Generate internal banking insights and allowlisted Gemini examples for an existing persona."""
from datetime import date
from pathlib import Path
import json
import sys
from finstats import load_transactions
from finstats.banking import banking_insights
from finstats.budgets import BudgetLimit
from .context import build_planning_context, context_json
from .schemas import AccountSnapshot, Goal


def write_examples(target):
    root=Path(__file__).resolve().parent.parent
    as_of=date(2026,11,15)
    txns=load_transactions(root/'data/mock_transactions.json','u003')
    profile=next(u for u in json.loads((root/'data/mock_users.json').read_text()) if u['user_id']=='u003')
    accounts=tuple(AccountSnapshot(a['account_id'],a['type'],a['balance'],as_of) for a in profile['accounts'])
    # Deliberately labelled demo-default, never asserted to be an existing user's budget.
    limits=(BudgetLimit('Eating out',250,source='demo_default'),)
    facts=banking_insights(txns,as_of=as_of,accounts=accounts,limits=limits)
    context=build_planning_context(txns,as_of=as_of,accounts=accounts,budget_limits=limits,
        goals=(Goal('example-holiday','holiday',6000,0,date(2027,7,15)),))
    target=Path(target);target.mkdir(parents=True,exist_ok=True)
    (target/'customer_u003_insights.json').write_text(json.dumps(facts,indent=2,sort_keys=True,allow_nan=False)+'\n')
    (target/'customer_u003_planning_context.json').write_text(context_json(context)+'\n')
    print('Generated synthetic u003 banking and model-bound examples.')


if __name__=='__main__':
    write_examples(sys.argv[1] if len(sys.argv)>1 else 'examples')
