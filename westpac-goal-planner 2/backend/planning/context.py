"""Allowlisted model boundary. No raw profiles, narratives or customer/account identifiers."""
from dataclasses import replace
from datetime import date, timedelta
from hashlib import sha256
import json
from finstats.banking import banking_insights
from finstats.shock_context import normalized_shocks
from finstats.savings import MonthlyFlow, savings_capacity_from_flows
from .schemas import Preferences, category, money


def _select(source, fields):
    return {k: source[k] for k in fields}


def build_planning_context(txns, *, as_of: date, goals=(), preferences=None,
                           accounts=(), pending_expenses=(), event_evidence=(),
                           budget_limits=(), feedback=(), subscription_states=None,
                           planned_expenses=(), event_states=None, known_holidays=()):
    preferences = preferences or Preferences()
    preferences = replace(preferences, protected_categories=tuple(sorted(
        set(preferences.protected_categories)|{b.category for b in budget_limits if b.protected})))
    past = [t for t in txns if t.date <= as_of]
    for t in past:
        money(t.amount, signed=True)
        if t.balance_after is not None:
            money(t.balance_after, signed=True)
    insights = banking_insights(past, as_of=as_of, accounts=accounts,
        protected_categories=preferences.protected_categories, limits=budget_limits,
        pending_expenses=pending_expenses, feedback=feedback,
        subscription_states=subscription_states, planned_expenses=planned_expenses,
        event_evidence=event_evidence,event_states=event_states,known_holidays=known_holidays)
    shocks = insights['shocks']
    posted_ids = {t.transaction_id for t in past}
    for shock in shocks:
        # Attribution on the SAME complete-month observation period. This is a numerical
        # counterfactual, not a suggested goal strategy and never another balance debit.
        event_rows = [t for t in past if t.transaction_id in shock["transaction_ids"] and t.is_expense]
        without = []
        for flow in insights["cashflow"]["monthly"]:
            rows = [t for t in event_rows if t.date.strftime("%Y-%m") == flow["month"]]
            without.append(MonthlyFlow(flow["month"], flow["income"],
                flow["essential"] - sum(t.spend for t in rows if t.essential),
                flow["discretionary"] - sum(t.spend for t in rows if not t.essential)))
        current = insights["cashflow"]["savings_capacity"]["comfortable"]
        shock["impact"]["comfortable_capacity_change_vs_excluding_event"] = (
            round(current - savings_capacity_from_flows(without).comfortable, 2) if event_rows else None)
        shock["impact"]["current_projected_balance"] = shock["impact"].get("projected_balance")
    pending = []
    seen = set()
    for e in pending_expenses:
        if e.known_on > as_of or e.transaction_id in posted_ids:
            continue
        if e.event_id in seen:
            raise ValueError("Duplicate pending event ID")
        seen.add(e.event_id)
        pending.append({"amount": money(e.amount), "category": category(e.category),
                        "due": e.due.isoformat(), "status": e.status,
                        "already_in_balance": False})
    # All nested structures are constructed explicitly, never copied from a customer object.
    observation = _select(insights["observation"], ("first", "last", "complete_months", "transactions"))
    income = _select(insights["income"], ("monthly_average", "regular_monthly_estimate",
        "irregular_monthly_observed", "variability_cv", "recent_three_month_average",
        "change_vs_earlier_months", "confidence", "confidence_basis"))
    categories = [{**_select(c, ("monthly_total", "monthly_discretionary", "recent_change")),
                   "category": category(c["category"])} for c in insights["spending"]["categories"]]
    # Merge unknown category labels; raw strings never cross this boundary.
    grouped = {}
    for c in categories:
        key = c["category"]
        if key not in grouped:
            grouped[key] = c.copy()
        else:
            for field in ("monthly_total", "monthly_discretionary", "recent_change"):
                grouped[key][field] = (round(grouped[key][field] + c[field], 2)
                                      if c[field] is not None and grouped[key][field] is not None else None)
    bills = [{**_select(b, ("typical_amount", "period_days", "next_expected", "upcoming_dates",
                           "recurrence", "confirmed_contract", "essential", "observations", "last_observed", "amount_basis")),
              "category": category(b["category"])} for b in insights["recurring"]]
    balances = [{**_select(b, ("kind", "balance", "observed_on", "protected", "goal_available", "designation_shortfall"))}
                for b in insights["balances"]]
    cashflow = insights["cashflow"]
    capacity = cashflow["savings_capacity"]
    runway = cashflow["runway"]
    due_pending = round(sum(e["amount"] for e in pending if e["status"] == "unpaid"
                           and e["due"] <= (runway["window_end"] if runway else (as_of + timedelta(days=14)).isoformat())), 2)
    model_runway = None
    if runway:
        model_runway = {**_select(runway, ("balance", "recurring_bills_due", "projected_balance",
                                          "window_end", "payday", "buffer", "below_default_buffer")),
                        "additional_known_pending_due": due_pending,
                        "projected_after_known_pending": round(runway["projected_balance"] - due_pending, 2),
                        "below_buffer_after_known_pending": runway["projected_balance"] - due_pending < runway["buffer"]}
    goal_ids = [g.goal_id for g in goals]
    if len(set(goal_ids)) != len(goal_ids):
        raise ValueError("Duplicate goal IDs")
    goal_rows = [{"goal_ref": f"goal-{i + 1}", "kind": g.kind, "target": money(g.target),
                  "allocated": money(g.allocated), "deadline": g.deadline.isoformat()}
                 for i, g in enumerate(goals)]
    uncertainty = list(insights["uncertainty"])
    if observation["complete_months"] < 3:
        uncertainty.append("Fewer than three complete months; capacity estimate has limited evidence.")
    if any(b["kind"] == "unknown" for b in balances):
        uncertainty.append("Some account kinds/restrictions are unknown; provide dated account snapshots.")
    if any(b["designation_shortfall"] > 0 for b in balances):
        uncertainty.append("Current balance no longer covers explicit account designations.")
    if any(g.deadline <= as_of for g in goals):
        uncertainty.append("A goal deadline is due or past; no future contribution period is assumed.")
    if sum(g.allocated for g in goals) > sum(b["goal_available"] for b in balances):
        uncertainty.append("Goal allocations exceed explicitly designated available savings; reconcile before planning.")
    if pending:
        uncertainty.append("Pending items linked to posted transactions or explicit bill references are reconciled; unlinked duplicates need user review.")
    return_payload = {
        "schema_version": "1.2", "as_of": as_of.isoformat(), "currency": "AUD",
        "financial_insights": {
            "observation": observation, "income": income,
            "spending": {"monthly_essential": insights["spending"]["monthly_essential"],
                         "monthly_discretionary": insights["spending"]["monthly_discretionary"],
                         "categories": list(grouped.values())},
            "cashflow": {"monthly": [_select(f, ("month", "income", "essential", "discretionary", "net"))
                                      for f in cashflow["monthly"]],
                         "average_surplus": cashflow["average_surplus"],
                         "month_to_date_net": cashflow["month_to_date_net"],
                         "savings_capacity": _select(capacity, ("comfortable", "stretch", "centre", "ewma_w",
                             "mean_w", "sigma_w", "income_cap", "mean_income", "mean_discretionary")),
                         "runway": model_runway},
            "recurring": bills, "balances": balances,
            "opportunities": [{**_select(o, ("kind", "observed_monthly_spend", "guaranteed_savings",
                                             "potentially_reducible_recurring_monthly", "assumption")),
                               "category": category(o["category"])} for o in insights["opportunities"]]},
        "shocks": [{**_select(s, ("date", "observed_on", "status", "event_amount", "detectors", "severity",
                                    "confirmed_hardship", "classification", "unexpected_confirmed", "expense_kind", "evidence_source", "uncertainty",
                                    "event_type", "detected_at", "detection_precision", "currency", "expectedness", "expectedness_source",
                                    "statistical_anomaly", "potential_disruption", "confidence", "pattern_stage",
                                    "lifecycle_status", "user_confirmation", "notification_required", "reassessment_required", "explanation", "questions")),
                    "event_ref": "event-"+sha256(s["event_ref"].encode()).hexdigest()[:16], "categories": sorted({category(c) for c in s["categories"]}),
                    "evidence": [_select(e, ("detector", "score", "score_is_unbounded")) for e in s["evidence"]],
                    "historical": {k:s['historical'][k] for k in ('category_observations','category_median','adaptive_floor','amount_vs_category_median','daily_purchase_count','baseline_daily_count','daily_count_threshold',
                        'observations','typical_amount','expected_date','grace_end','arrival_date','expected_amount','received_amount',
                        'shortfall','period_days','training_observations','missed_cycles','reduced_cycles','baseline_weekly','recent_weekly',
                        'threshold','weeks','essential','previous_amount','recent_amount','earlier_income','recent_income',
                        'earlier_cv','recent_cv','pressure_observations','due_date') if k in s['historical']},
                    "impact": _select(s["impact"], ("already_in_balance", "additional_future_debit", "observed_expense",
                        "projected_buffer_shortfall", "savings_capacity_effect", "comfortable_capacity_change_vs_excluding_event", "current_projected_balance", "current_balance", "protected_reserve", "upcoming_obligations", "projected_balance", "unreserved_projected_balance", "liquidity_ratio",
                        "comfortable", "stretch", "monthly_surplus", "income_variability", "amounts_additive", "already_in_projection"))} for i, s in enumerate(shocks)],
        "pending_expenses": pending, "goals": goal_rows,
        "preferences": {"protected_categories": list(preferences.protected_categories),
                        "allow_deadline_extension": preferences.allow_deadline_extension,
                        "allow_discretionary_changes": preferences.allow_discretionary_changes},
        "uncertainty": uncertainty,
    }
    # New feature adapters remain allowlisted all the way down. No internal bill,
    # customer, subscription or transaction identifiers leave the trusted layer.
    calendar = insights['bills_calendar']
    return_payload['financial_insights']['budgets'] = {
        'month': insights['budgets']['month'],
        'categories': [{**_select(b, ('monthly_limit','spent','essential_spent','discretionary_spent',
            'remaining','protected','source','status','upcoming_obligations_this_month','remaining_is_cash_available')),
            'category': category(b['category'])} for b in insights['budgets']['categories']]}
    return_payload['financial_insights']['bills_calendar'] = {
        'items': [{**_select(b, ('expected_amount','expected_date','date_kind','frequency','period_days',
                               'essential','confirmed','recurrence_source','confidence','source','status','already_paid','amount_basis')),
                   'bill_ref': f'bill-{i+1}', 'category': category(b['category'])}
                  for i,b in enumerate(calendar['items'])],
        'summary_days': {h:_select(v,('confirmed_due','estimated_due','total_expected','anticipated'))
                         for h,v in calendar['summary_days'].items()}}
    evidence_fields = ('monthly_discretionary','monthly_average','months','minimum_observed','monthly_std',
        'typical_payment','period_days','observations','previous_payment','current_payment','change_date',
        'distinct_series','overlap_confirmed')
    return_payload['financial_insights']['savings_finder'] = [
        {**_select(o,('kind','confidence','requires_confirmation','essential','protected','savings_range_basis')),
         'opportunity_ref':f'opportunity-{i+1}','category':category(o['category']),
         'monthly_savings_range':_select(o['monthly_savings_range'],('minimum','maximum')),
         'evidence':{k:o['evidence'][k] for k in evidence_fields if k in o['evidence']}}
        for i,o in enumerate(insights['savings_finder'])]
    value_fields = ('monthly_average','recent_change','spent','monthly_limit','remaining','event_amount',
        'event_date','detector_count','balance','obligations','projected','buffer','earlier_cv','recent_cv',
        'observed_monthly_spend','requires_confirmation')
    return_payload['financial_insights']['personalised_insights'] = [
        {**_select(i,('type','severity','confidence','explanation','actionable')),
         'insight_ref':f'insight-{n+1}','category':category(i['category']),
         'observation_period':_select(i['observation_period'],('start','end')),
         'values':{k:i['values'][k] for k in value_fields if k in i['values']}}
        for n,i in enumerate(insights['personalised_insights'])]
    rejected = {}
    for item in feedback:
        if item.get('action')=='reject_adjustment' and item.get('category') in preferences.protected_categories:
            continue
        if item.get('action')=='reject_adjustment':
            cat=category(item.get('category'))
            rejected[cat]=rejected.get(cat,0)+1
    return_payload['preferences']['unwanted_reduction_categories'] = sorted(c for c,n in rejected.items() if n>=2)
    return_payload['preferences']['feedback_summary'] = {
        action:sum(1 for f in feedback if f.get('action')==action)
        for action in ('useful','not_useful','dismiss','reject_adjustment')}
    # Use the reconciled calendar for the SAME account and runway window. Cancellation
    # and bill replacement affect only estimates; posted balances/history stay intact.
    if runway:
        relevant=[b for b in calendar['items'] if b['status']=='upcoming'
                  and b['expected_date']<=runway['window_end']
                  and b['account_id'] in {None,runway['account_id']}]
        recurring_due=round(sum(b['expected_amount'] for b in relevant if b['source']=='recurring_estimate'),2)
        pending_due=round(sum(b['expected_amount'] for b in relevant if b['source']=='explicit_obligation'),2)
        model_runway.update(recurring_bills_due=recurring_due,
            projected_balance=round(runway['balance']-recurring_due,2),
            additional_known_pending_due=pending_due,
            projected_after_known_pending=round(runway['balance']-recurring_due-pending_due,2),
            below_default_buffer=runway['balance']-recurring_due<runway['buffer'],
            below_buffer_after_known_pending=runway['balance']-recurring_due-pending_due<runway['buffer'])
    encoded = json.dumps(return_payload, sort_keys=True, allow_nan=False, separators=(',', ':'))
    return_payload["state_version"] = sha256(encoded.encode()).hexdigest()
    return return_payload


def context_json(context):
    return json.dumps(context, indent=2, sort_keys=True, allow_nan=False)
