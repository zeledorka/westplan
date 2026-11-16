"""Customer-scoped append-only SQLite snapshots and atomic optimistic writes."""
import json
import sqlite3
from datetime import date


def empty_state():
    return {'budgets':{},'corrections':{},'transaction_corrections':{},'feedback':[],
            'protected_categories':[],'goals':{},'proposals':{},'subscriptions':{},'shock_events':{}}


class StaleState(ValueError):
    pass


class DemoStore:
    def __init__(self, path):
        self.path=str(path)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS snapshots(customer TEXT NOT NULL, revision INTEGER NOT NULL, effective_on TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(customer,revision))')

    def read(self, customer, as_of: date):
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT revision,payload FROM snapshots WHERE customer=? AND effective_on<=? ORDER BY revision DESC LIMIT 1',
                           (customer,as_of.isoformat())).fetchone()
        return (row[0],json.loads(row[1])) if row else (0,empty_state())

    def update(self, customer, *, as_of: date, expected_revision, mutate):
        if isinstance(expected_revision,bool) or not isinstance(expected_revision,int) or expected_revision<0:
            raise ValueError('Expected revision must be a nonnegative integer')
        with sqlite3.connect(self.path,timeout=10) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision,effective_on,payload FROM snapshots WHERE customer=? ORDER BY revision DESC LIMIT 1',(customer,)).fetchone()
            revision=row[0] if row else 0
            if revision!=expected_revision or row and row[1]>as_of.isoformat():
                raise StaleState('State changed or write would backdate history')
            state=json.loads(row[2]) if row else empty_state()
            result=mutate(state)
            db.execute('INSERT INTO snapshots VALUES (?,?,?,?)',(customer,revision+1,as_of.isoformat(),json.dumps(state,allow_nan=False)))
            return result

    def history(self, customer, as_of):
        """Internal dated snapshots for goal audit rendering, never a cross-customer query."""
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT revision,effective_on,payload FROM snapshots WHERE customer=? AND effective_on<=? ORDER BY revision',
                            (customer,as_of.isoformat())).fetchall()
        return [(r,d,json.loads(payload)) for r,d,payload in rows]
