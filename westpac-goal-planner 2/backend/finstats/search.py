"""Four constrained read-only searches. No SQL, code execution or AI interpretation."""
from dataclasses import dataclass
from datetime import date, timedelta
import re
from .contracts import CATEGORIES
from .bills import recurring_series
from .shocks import detect_recurring_bill_changes


@dataclass(frozen=True)
class TransactionQuery:
    kind: str
    start: date
    end: date
    category: str | None = None
    limit: int = 10

    def __post_init__(self):
        if self.kind not in {'spending_total','largest_expenses','subscription_increases'} or self.start>self.end:
            raise ValueError('Unsupported query or invalid dates')
        if self.category is not None and self.category not in CATEGORIES:
            raise ValueError('Unknown search category')
        if isinstance(self.limit,bool) or not isinstance(self.limit,int) or not 1<=self.limit<=50:
            raise ValueError('Result limit must be 1..50')


def parse_question(question, *, as_of):
    q=question.strip().lower().rstrip('?')
    if q=='which subscriptions increased in price':
        return TransactionQuery('subscription_increases',date(as_of.year,1,1),as_of)
    if q=='what were my largest expenses this year':
        return TransactionQuery('largest_expenses',date(as_of.year,1,1),as_of)
    match=re.fullmatch(r'how much did i spend on (groceries|transport) (last month|over the last three months)',q)
    if not match:
        raise ValueError('Unsupported question; use a documented search template')
    end=as_of.replace(day=1)-timedelta(days=1)
    months=1 if match[2]=='last month' else 3
    index=end.year*12+end.month-months
    start=date(index//12,index%12+1,1)
    return TransactionQuery('spending_total',start,end,match[1].title())


def search_transactions(txns, query, *, as_of):
    end=min(query.end,as_of)
    past=[t for t in txns if t.date<=end]
    rows=[t for t in past if query.start<=t.date and t.is_expense
          and (query.category is None or t.category==query.category)]
    if query.kind=='spending_total':
        result={'total_spent':round(sum(-t.amount for t in rows),2),'transaction_count':len(rows)}
    elif query.kind=='largest_expenses':
        result={'transactions':[{'date':t.date.isoformat(),'category':t.category,'amount':round(t.spend,2)}
                 for t in sorted((t for t in rows if t.amount<0),key=lambda t:t.spend,reverse=True)[:query.limit]]}
    else:
        changes=[c for s in recurring_series(past,end) if s.category=='Subscriptions'
                 for c in detect_recurring_bill_changes([],series=[s])
                 if query.start<=c.date<=end and c.score>0]
        result={'changes':[{'date':c.date.isoformat(),'previous':c.details['previous'],'current':c.amount}
                            for c in changes[:query.limit]]}
    return {'query':query.kind,'start':query.start.isoformat(),'end':end.isoformat(),'category':query.category,**result}
