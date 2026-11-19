"""Deterministic import suggestions. Source categories win unless explicitly corrected."""
from dataclasses import dataclass, replace
import re
from .data import normalize_merchant
from .contracts import CATEGORIES


@dataclass(frozen=True)
class Classification:
    category: str
    essential: bool | None
    confidence: float
    source: str
    uncertain: bool


# Merchant prefixes must match full tokens, not arbitrary substrings. No generic
# marketplace rule: Amazon, Apple billing and Bunnings can represent many purchases.
RULES = (
    ('Groceries',True,('WOOLWORTHS','COLES','ALDI STORES','IGA','HARRIS FARM MARKETS')),
    ('Eating out',False,('UBER EATS','MCDONALDS','GUZMAN Y GOMEZ','MAD MEX','THE COFFEE CLUB','ROSIES CAFE','BAKERS DELIGHT','EL JANNAH')),
    ('Subscriptions',False,('NETFLIX COM','SPOTIFY','KAYO SPORTS','DISNEY PLUS')),
    ('Health',False,('ANYTIME FITNESS','F TRAINING')),
    ('Health',True,('CHEMIST WAREHOUSE','TERRYWHITE CHEMMART','PRICELINE PHARMACY','OPSM','SPECSAVERS')),
    ('Transport',True,('AMPOL','BP','LINKT TOLLS','TRANSPORT FOR NSW OPAL','CABS','SERVICE NSW VEHICLE REGISTRATION')),
    ('Utilities',True,('TELSTRA MOBILE','TELSTRA INTERNET','AMAYSIM MOBILE','TPG INTERNET','OPTUS MOBILE','BELONG INTERNET','SYDNEY WATER','AGL ENERGY','ORIGIN ENERGY','SPLIT BILLS POWER AND INTERNET')),
    ('Housing',True,('RENT SHARE HOUSE','RENT RAY WHITE PROPERTY MGMT','MOSMAN COUNCIL RATES','PENRITH CITY COUNCIL RATES')),
    ('Loan repayments',True,('HOME LOAN REPAYMENT','BUSINESS LOAN REPAYMENT','TOYOTA FINANCE HILUX')),
    ('Entertainment',False,('DAN MURPHYS','HOYTS','EVENT CINEMAS','TICKETEK','THE HENSON','HOLEY MOLEY')),
    ('Insurance',True,('HCF HEALTH INSURANCE','BUPA HEALTH INSURANCE','NRMA CONTENTS INSURANCE','AAMI HOME AND CAR INSURANCE','QBE PUBLIC LIABILITY INSURANCE')),
)


def committed_category(transaction):
    if not transaction.is_expense:return None
    if transaction.category in {'Buy Now Pay Later','Loan repayments'}:return transaction.category
    # Transaction.merchant is cached; causal-history scans reuse the same rows many times.
    merchant=getattr(transaction,'merchant',None)
    if merchant is None:merchant=normalize_merchant(transaction.description)
    if re.match(r'^(AFTERPAY|ZIP PAY|ZIP MONEY)( |$)',merchant):return 'Buy Now Pay Later'
    if re.match(r'^(HOME LOAN REPAYMENT|BUSINESS LOAN REPAYMENT|CREDIT CARD REPAYMENT)( |$)',merchant):return 'Loan repayments'
    return None


def committed_transactions(txns):
    """Normalise only evidenced repayments; preserve all other source classifications."""
    rows=[]
    for transaction in txns:
        category=committed_category(transaction)
        rows.append(replace(transaction,category=category,essential=True)
            if category and (transaction.category!=category or not transaction.essential) else transaction)
    return rows


def classify_transaction(transaction, *, merchant_corrections=None, explicit_correction=None):
    category=committed_category(transaction)
    if category:return Classification(category,True,1,'committed_repayment',False)
    if explicit_correction is not None:
        c=explicit_correction
        if c['category'] not in CATEGORIES or not isinstance(c['essential'],bool):
            raise ValueError('Invalid manual category correction')
        return Classification(c['category'],c['essential'],1,'manual_transaction',False)
    if transaction.category and transaction.category.lower() not in {'uncategorised','uncategorized','unknown'}:
        return Classification(transaction.category,transaction.essential,1,'explicit_source',False)
    key=normalize_merchant(transaction.description)
    correction=(merchant_corrections or {}).get(key)
    if correction is not None:
        if correction['category'] not in CATEGORIES or not isinstance(correction['essential'],bool):
            raise ValueError('Invalid merchant correction')
        return Classification(correction['category'],correction['essential'],1,'manual_merchant',False)
    if transaction.type=='transfer':
        return Classification('Transfers',False,1,'transaction_type',False)
    if transaction.is_income:
        return Classification('Income',False,1,'transaction_type',False)
    for cat,essential,merchants in RULES:
        if any(key==m or key.startswith(m+' ') for m in merchants):
            return Classification(cat,essential,.95,'merchant_rule',False)
    return Classification('Other',None,0,'uncertain',True)


def classified_transactions(txns, *, merchant_corrections=None, transaction_corrections=None):
    out=[]
    for t in txns:
        c=classify_transaction(t,merchant_corrections=merchant_corrections,
                               explicit_correction=(transaction_corrections or {}).get(t.transaction_id))
        # Unknown essentiality is conservative: not an automatic discretionary saving.
        out.append(replace(t,category=c.category,essential=c.essential if c.essential is not None else True))
    return out
