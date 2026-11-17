"""Pure overlays of explicitly recorded customer actions; detection remains independent."""
from datetime import date

ACTIONS={'acknowledge':'acknowledged','confirm_expected':'confirmed_expected',
         'confirm_unexpected':'confirmed_unexpected','dismiss':'dismissed','resolve':'resolved','reopen':'new'}


def apply_lifecycle(events, states, *, as_of):
    for e in events:
        record=states.get(e['event_ref'])
        # Remember expected context only for comparable amounts and established recurrence
        # or a later annual occurrence; never remember an entire category or income failure.
        for prior in states.values():
            if (prior.get('known_on','9999')<=as_of.isoformat() and e.get('_comparison_key') and prior.get('comparison_key')==e['_comparison_key']
                and prior.get('remember_expected') and prior.get('expectedness')=='expected'
                and prior.get('amount') and e['event_amount']
                and abs(e['event_amount']-prior['amount'])<=.2*prior['amount']
                and (('recurring_bill_change' in e['detectors']) or
                     (date.fromisoformat(e['date'])-date.fromisoformat(prior['event_date'])).days>=300)):
                e['expectedness']='expected';e['expectedness_source']='remembered_user_confirmation'
                if e['potential_disruption']!='high':
                    e['lifecycle_status']='confirmed_expected';e['user_confirmation']='remembered_expected'
        if record and record.get('known_on','9999')<=as_of.isoformat():
            e['lifecycle_status']=record['status']
            e['user_confirmation']=record.get('expectedness') if record.get('expectedness') in {'expected','unexpected'} else 'none'
            if record.get('expectedness') in {'expected','unexpected'}:
                e['expectedness']=record['expectedness'];e['expectedness_source']='user_confirmation'
            changed=record.get('evidence_version')!=e['evidence_version']
            e['reassessment_required']=changed
            if changed:
                e['lifecycle_status']='new'
        e['unexpected_confirmed']=e['expectedness']=='unexpected'
        e['notification_required']=e['lifecycle_status']=='new'
    return events
