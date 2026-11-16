"""Shared deterministic event shape; statistical confidence is never a probability."""
from hashlib import sha256
import json


def event_id(*parts):
    return sha256(json.dumps(parts, separators=(',', ':'), default=str).encode()).hexdigest()[:24]


def event(key, kind, day, observed, amount=None, categories=(), transaction_ids=(), methods=(), status='posted'):
    return {
        'event_ref': event_id(*key), 'event_type': kind,
        'date': day.isoformat(), 'observed_on': observed.isoformat(),
        'detected_at': observed.isoformat(), 'detection_precision': 'day', 'currency': 'AUD',
        'status': status, 'event_amount': round(amount, 2) if amount is not None else None,
        'categories': sorted(set(categories)), 'transaction_ids': sorted(set(transaction_ids)),
        'detectors': sorted(set(methods)), 'evidence': [], 'historical': {},
        'statistical_anomaly': False, 'potential_disruption': 'unknown',
        'expectedness': 'unknown', 'expectedness_source': 'insufficient_evidence',
        'unexpected_confirmed': False, 'confirmed_hardship': False,
        'confidence': 'limited', 'pattern_stage': 'one_off',
        'severity': 'review', 'classification': kind, 'expense_kind': 'unknown',
        'evidence_source': 'detector_only', 'lifecycle_status': 'new',
        'user_confirmation': 'none', 'notification_required': True, 'reassessment_required': False,
        'impact': {'already_in_balance': status in {'posted','income_received','income_delayed'}, 'already_in_projection':False, 'additional_future_debit': 0,
                   'observed_expense': round(amount,2) if status=='posted' and amount is not None else None,
                   'projected_buffer_shortfall': None,
                   'savings_capacity_effect': 'Posted expenses enter completed-month history once; no extra balance deduction.'},
        'explanation': 'Financial activity changed; its significance needs context.',
        'questions': ['Was this expected?'],
        'uncertainty': 'An anomaly is not confirmation of surprise, fraud or financial hardship.',
    }
