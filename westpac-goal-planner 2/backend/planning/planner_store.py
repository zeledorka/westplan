"""Draft-only conversation records in the existing SQLite database, outside goal snapshots."""
import json
import sqlite3

class PlannerStore:
    def __init__(self, store):
        self.path=store.path
        with sqlite3.connect(self.path) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS planner_turns (
                customer TEXT NOT NULL, session_id TEXT NOT NULL, request_id TEXT NOT NULL,
                goal_id TEXT NOT NULL, created_at REAL NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(customer,request_id))''')

    def request(self, customer, request_id):
        with sqlite3.connect(self.path) as db:
            row=db.execute('SELECT payload FROM planner_turns WHERE customer=? AND request_id=?',(customer,request_id)).fetchone()
        return json.loads(row[0]) if row else None

    def history(self, customer, session_id=None):
        with sqlite3.connect(self.path) as db:
            rows=db.execute('SELECT payload FROM planner_turns WHERE customer=?'+(' AND session_id=?' if session_id else '')+
                ' ORDER BY created_at DESC LIMIT 40',(customer,session_id) if session_id else (customer,)).fetchall()
        return list(reversed([json.loads(r[0]) for r in rows]))

    def count_recent(self, customer, since, exclude_request_id=''):
        with sqlite3.connect(self.path) as db:
            return db.execute('SELECT COUNT(*) FROM planner_turns WHERE customer=? AND created_at>? AND request_id!=?',(customer,since,exclude_request_id)).fetchone()[0]

    def save(self, customer, record):
        def replace(old):
            from .gemini import PlannerError
            if old and old.get('run_id')!=record.get('run_id'):
                raise PlannerError('planner_busy','Another execution owns this request.',409)
            if old and old.get('cancel_requested') and record['status']=='draft_unverified':
                raise PlannerError('planner_cancelled','Planning cancelled. No goals changed.',409)
            return {**record,'cancel_requested':bool(old and old.get('cancel_requested'))}
        return self.alter(customer,record['request_id'],replace)

    def alter(self, customer, request_id, change):
        """Customer-bound compare/update under a SQLite write transaction."""
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT payload FROM planner_turns WHERE customer=? AND request_id=?',(customer,request_id)).fetchone()
            old=json.loads(row[0]) if row else None
            record=change(old)
            db.execute('INSERT INTO planner_turns VALUES (?,?,?,?,?,?) ON CONFLICT(customer,request_id) DO UPDATE SET session_id=excluded.session_id,goal_id=excluded.goal_id,created_at=excluded.created_at,payload=excluded.payload',
                (customer,record['session_id'],request_id,record['goal_id'],record['created_at'],json.dumps(record,allow_nan=False)))
            return record
