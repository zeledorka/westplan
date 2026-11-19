"""Account-scoped recurrence estimates using only observations available at as_of."""
from datetime import date
from .shocks import detect_recurring_series


def recurring_series(txns, as_of: date, kind="expense"):
    past = [t for t in txns if t.date <= as_of]
    return [s for account in sorted({t.account_id for t in past})
            for s in detect_recurring_series(
                [t for t in past if t.account_id == account], kind=kind)]



def expected_payment(series):
    """Use a detected latest price step; otherwise retain the robust median estimate."""
    from .shocks import detect_recurring_bill_changes
    changes=detect_recurring_bill_changes([],series=[series])
    if changes and changes[-1].date==series.occurrences[-1].date:
        return changes[-1].amount, 'latest_detected_price_change'
    return round(series.typical_amount,2), 'recent_median'


def bill_insights(txns, as_of: date, horizon_days=30):
    out = []
    for s in recurring_series(txns, as_of):
        dates = s.upcoming(as_of, horizon_days)
        amount,basis = expected_payment(s)
        out.append({
            "category": s.category, "account_id": s.account_id,
            "typical_amount": amount, "amount_basis": basis,
            "period_days": s.period_days,
            "next_expected": dates[0].isoformat() if dates else None,
            "upcoming_dates": [d.isoformat() for d in dates],
            "recurrence": "source_flagged" if all(t.recurring for t in s.occurrences) else "inferred",
            "confirmed_contract": False,
            "essential": any(t.essential for t in s.occurrences),
            "observations": len(s.occurrences),
            "last_observed": s.occurrences[-1].date.isoformat(),
            "merchant": s.merchant,
        })
    return out


def subscription_id(account_id, merchant):
    """Internal opaque series key, stable across dates and price changes."""
    from hashlib import sha256
    return sha256((account_id + '\0' + merchant).encode()).hexdigest()[:20]


def bills_calendar(txns, *, as_of: date, pending_expenses=(), subscription_states=None, horizon_days=30):
    """Paid history and upcoming occurrences. Explicit links reconcile one-off obligations."""
    from datetime import timedelta
    from hashlib import sha256
    states = subscription_states or {}
    past = [t for t in txns if t.date <= as_of]
    posted = {t.transaction_id for t in past}
    if len(posted)!=len(past) or any(not t.transaction_id for t in past):
        raise ValueError('Calendar requires unique nonempty transaction IDs')
    items = []
    for s in recurring_series(past, as_of):
        sid = subscription_id(s.account_id, s.merchant)
        state = states.get(sid, {})
        cancelled = state.get('status') == 'cancelled' and state.get('effective_on', '9999') <= as_of.isoformat()
        frequency = ('weekly' if 6 <= s.period_days <= 8 else 'fortnightly' if 12 <= s.period_days <= 16
                     else 'monthly' if 27 <= s.period_days <= 32 else 'other')
        common = {'subscription_id': sid, 'category': s.category, 'merchant': s.merchant,
                  'account_id': s.account_id, 'frequency': frequency, 'period_days': s.period_days,
                  'essential': any(t.essential for t in s.occurrences),
                  'confirmed': False, 'recurrence_source': 'source_flagged' if all(t.recurring for t in s.occurrences) else 'inferred',
                  'confidence': 'higher' if len(s.occurrences) >= 6 else 'moderate'}
        amount,basis=expected_payment(s)
        last = s.occurrences[-1]
        items.append({**common, 'bill_id': 'paid-'+last.transaction_id, 'status': 'paid',
                      'expected_date': last.date.isoformat(), 'date_kind': 'observed_posting',
                      'expected_amount': round(last.spend, 2), 'amount_basis':'observed_posting', 'source': 'posted_transaction', 'already_paid': True})
        if not cancelled:
            for d in s.upcoming(as_of, horizon_days):
                bid = sha256((sid+d.isoformat()).encode()).hexdigest()[:20]
                items.append({**common, 'bill_id': bid, 'status': 'upcoming', 'expected_date': d.isoformat(),
                              'date_kind': 'estimated', 'expected_amount': amount, 'amount_basis': basis,
                              'source': 'recurring_estimate', 'already_paid': False})
    seen = set()
    for p in pending_expenses:
        if p.known_on > as_of or p.transaction_id in posted:
            continue
        if p.event_id in seen:
            raise ValueError('Duplicate pending obligation')
        seen.add(p.event_id)
        if p.replaces_bill_id:
            if p.status != "unpaid":
                raise ValueError("Only confirmed unpaid obligations can replace an estimated bill")
            matching = [b for b in items if b['bill_id'] == p.replaces_bill_id and b['status'] == 'upcoming']
            if not matching:
                raise ValueError('Replacement must reference an upcoming bill in this calendar')
            items = [b for b in items if b not in matching]
        items.append({'bill_id': 'pending-'+p.event_id, 'subscription_id': None,
                      'category': p.category, 'merchant': None, 'account_id': None, 'frequency': 'one_off',
                      'period_days': None, 'expected_amount': p.amount, 'amount_basis':'explicit_obligation', 'expected_date': p.due.isoformat(),
                      'date_kind': 'confirmed_due' if p.status == 'unpaid' else 'anticipated',
                      'confirmed': p.status == 'unpaid', 'recurrence_source': 'explicit_obligation',
                      'confidence': 'higher' if p.status == 'unpaid' else 'limited',
                      'essential': True, 'source': 'explicit_obligation', 'status': 'upcoming' if p.status == 'unpaid' else 'anticipated',
                      'already_paid': False})
    summary = {}
    for horizon in (7,14,30):
        eligible = [b for b in items if not b['already_paid'] and b['expected_date'] <= (as_of+timedelta(days=horizon)).isoformat()]
        confirmed = sum(b['expected_amount'] for b in eligible if b['confirmed'])
        estimated = sum(b['expected_amount'] for b in eligible if b['status'] == 'upcoming' and not b['confirmed'])
        summary[str(horizon)] = {'confirmed_due': round(confirmed,2), 'estimated_due': round(estimated,2),
                                'total_expected': round(confirmed+estimated,2),
                                'anticipated': round(sum(b['expected_amount'] for b in eligible if b['status']=='anticipated'),2)}
    return {'as_of': as_of.isoformat(), 'items': sorted(items,key=lambda b:(b['expected_date'],b['bill_id'])),
            'summary_days': summary, 'assumption': 'Overdue unpaid obligations included; estimated dates are not confirmed contracts.'}
