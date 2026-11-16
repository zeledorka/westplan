"""Generate five synthetic customers with one year of transactions each.

Everything here is invented. Re-run to regenerate (output is identical each time for the same SEED):

    python generate_mock_data.py

Writes, next to this file:
    mock_users.json          profiles, demo logins and accounts
    mock_transactions.json   every transaction for every user (flat list)
    statements/<username>.csv  one CSV statement per user
and, for the frontend:
    ../../frontend/src/data/mockUsers.json   small per-user summary the app shell displays
"""
import csv
import json
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 2026
END = date(2026, 11, 15)
START = date(2025, 11, 16)
HERE = Path(__file__).parent
DAYS = [START + timedelta(n) for n in range((END - START).days + 1)]

GROCERS = ["WOOLWORTHS", "COLES", "ALDI STORES", "IGA"]
SUBURB = {"u001": "NEWTOWN", "u002": "MOSMAN", "u003": "PENRITH", "u004": "GOSFORD", "u005": "MARRICKVILLE"}


class Ledger:
    def __init__(self, uid, rng):
        self.uid, self.rng, self.rows = uid, rng, []

    def add(self, d, desc, amount, category, essential=False, recurring=False, account="everyday", kind=None):
        if not (START <= d <= END):
            return
        kind = kind or ("income" if amount > 0 else "expense")
        self.rows.append(dict(date=d, description=desc, amount=round(amount, 2), category=category,
                              type=kind, essential=essential, recurring=recurring, account=account))

    # --- recurring schedules -------------------------------------------------
    def every(self, n_days, first_offset, desc, amount, category, essential=True, jitter=0.0, scale=None):
        d = START + timedelta(first_offset)
        while d <= END:
            a = amount * (1 + self.rng.uniform(-jitter, jitter)) * (scale(d) if scale else 1)
            self.add(d, desc, a, category, essential, True)
            d += timedelta(n_days)

    def monthly(self, day, desc, amount, category, essential=True, jitter=0.0, months=None):
        y, m = START.year, START.month
        while date(y, m, 1) <= END:
            if months is None or m in months:
                a = amount * (1 + self.rng.uniform(-jitter, jitter))
                self.add(date(y, m, min(day, 28)), desc, a, category, essential, True)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    def once(self, month, day, desc, amount, category, essential=False):
        for y in (START.year, END.year):
            self.add(date(y, month, day), desc, amount, category, essential, False)

    # --- everyday card spending ---------------------------------------------
    def spend(self, category, merchants, per_week, lo, hi, essential=False, weekend=1.0, december=1.0):
        for d in DAYS:
            rate = per_week / 7 * (weekend if d.weekday() >= 5 else 1) * (december if d.month == 12 else 1)
            n = int(rate) + (self.rng.random() < rate - int(rate))
            for _ in range(n):
                amt = self.rng.triangular(lo, hi, lo + (hi - lo) * 0.3)
                m = self.rng.choice(merchants)
                if m in GROCERS:
                    m = f"{m} {SUBURB[self.uid]}"
                self.add(d, m, -amt, category, essential)

    # --- injected shocks (fixed values, no RNG use, so the rest of the data is unchanged) ---
    def reprice(self, desc, from_date, amount):
        """Change a recurring charge's amount from from_date onward (e.g. a rate rise)."""
        for r in self.rows:
            if r["description"] == desc and r["date"] >= from_date:
                r["amount"] = round(amount, 2)

    def save(self, day, amount, label="TRANSFER TO ESAVER"):
        y, m = START.year, START.month
        while date(y, m, 1) <= END:
            d = date(y, m, day)
            self.add(d, label, -amount, "Transfers", False, True, kind="transfer")
            self.add(d, "TRANSFER FROM CHOICE", amount, "Transfers", False, True, account="savings", kind="transfer")
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)


# ---------------------------------------------------------------------------
# Personas
# ---------------------------------------------------------------------------
def mia(L):  # student, part-time job
    summer = lambda d: 1.45 if d.month in (12, 1, 2) else 1.0
    L.every(7, 4, "BEAN SCENE CAFE WAGES", 365, "Income", jitter=0.25, scale=summer)
    L.every(14, 9, "CENTRELINK YOUTH ALLOWANCE", 360.40, "Income")
    L.every(7, 1, "RENT SHARE HOUSE NEWTOWN", -230, "Housing")
    L.monthly(12, "SPLIT BILLS POWER AND INTERNET", -65, "Utilities", jitter=0.2)
    L.monthly(5, "AMAYSIM MOBILE", -30, "Utilities")
    L.monthly(8, "SPOTIFY STUDENT", -6.99, "Subscriptions", False)
    L.monthly(17, "NETFLIX.COM", -9.99, "Subscriptions", False)
    L.every(14, 3, "ANYTIME FITNESS", -17.95, "Health", False)
    L.monthly(24, "UNI CO-OP BOOKSHOP", -215, "Education", jitter=0.15, months=[2, 7])
    L.once(3, 14, "UNIVERSITY STUDENT SERVICES FEE", -175, "Education", True)
    L.spend("Groceries", ["ALDI STORES", "WOOLWORTHS", "COLES"], 2.2, 14, 78, True)
    L.spend("Eating out", ["GUZMAN Y GOMEZ", "UBER *EATS", "CAMPUS CAFE", "MAD MEX", "MCDONALDS", "BOOST JUICE"], 3.6, 6, 28, weekend=1.3)
    L.spend("Transport", ["TRANSPORT FOR NSW OPAL"], 1.0, 20, 40, True)
    L.spend("Entertainment", ["EVENT CINEMAS", "TICKETEK", "THE COURTHOUSE HOTEL", "STEAM GAMES"], 0.8, 12, 62, weekend=1.8)
    L.spend("Shopping", ["KMART", "COTTON ON", "UNIQLO", "ASOS.COM", "OFFICEWORKS"], 0.5, 15, 110, december=1.6)
    L.spend("Health", ["CHEMIST WAREHOUSE", "PRICELINE PHARMACY"], 0.15, 8, 35, True)
    L.save(20, 100)
    # SHOCK (burst): card details skimmed, nine small online charges in one day
    for desc, amt in [("PAYPAL *GAMEKEYS", 4.99), ("PAYPAL *GAMEKEYS", 9.99), ("APPLE.COM/BILL", 1.49),
                      ("GOOGLE *TEMU", 12.40), ("GOOGLE *TEMU", 18.75), ("PAYPAL *VPNSHIELD", 7.99),
                      ("STEAM GAMES", 14.50), ("ASOS.COM", 22.00), ("PAYPAL *GAMEKEYS", 19.99)]:
        L.add(date(2026, 8, 22), desc, -amt, "Shopping")


def priya(L):  # high income, business owner, home loan + business loan, two kids
    L.every(14, 2, "SHARMA PHYSIOTHERAPY PTY LTD SALARY", 8200, "Income")
    L.monthly(15, "SHARMA PHYSIOTHERAPY DIVIDEND", 15000, "Income", months=[1, 4, 7, 10])
    L.monthly(3, "HOME LOAN REPAYMENT", -7200, "Loan repayments")
    L.monthly(10, "BUSINESS LOAN REPAYMENT", -3400, "Loan repayments")
    L.monthly(20, "ST CATHERINES SCHOOL FEES", -3600, "Kids", months=[1, 4, 7, 10])
    L.monthly(20, "NORTHSIDE GRAMMAR SCHOOL FEES", -3600, "Kids", months=[1, 4, 7, 10])
    L.every(7, 2, "CAMP AUSTRALIA OSHC", -185, "Kids")
    L.monthly(6, "AQUA SWIM SCHOOL", -220, "Kids", False)
    L.monthly(14, "MOSMAN COUNCIL RATES", -780, "Housing", months=[2, 5, 8, 11])
    L.monthly(22, "AGL ENERGY", -640, "Utilities", jitter=0.18, months=[1, 4, 7, 10])
    L.monthly(26, "SYDNEY WATER", -310, "Utilities", jitter=0.1, months=[3, 6, 9, 12])
    L.monthly(9, "TELSTRA INTERNET", -110, "Utilities")
    L.monthly(9, "TELSTRA MOBILE", -189, "Utilities")
    L.monthly(1, "BUPA HEALTH INSURANCE", -412, "Insurance")
    L.monthly(18, "NRMA HOME AND MOTOR INSURANCE", -298, "Insurance")
    L.once(3, 11, "SERVICE NSW VEHICLE REGISTRATION", -1120, "Transport", True)
    L.every(14, 5, "SPARKLE HOME CLEANING", -160, "Housing", False)
    L.every(7, 3, "F45 TRAINING", -68, "Health", False)
    for day, name, amt in [(4, "NETFLIX.COM", 25.99), (7, "DISNEY PLUS", 13.99), (11, "SPOTIFY FAMILY", 23.99), (13, "APPLE.COM/BILL", 4.49)]:
        L.monthly(day, name, -amt, "Subscriptions", False)
    L.once(7, 2, "QANTAS AIRWAYS", -3850, "Travel")
    L.once(7, 9, "SHERATON GRAND MIRAGE", -2900, "Travel")
    L.once(1, 6, "NOVOTEL WOLLONGONG", -1650, "Travel")
    L.spend("Groceries", ["WOOLWORTHS", "COLES", "HARRIS FARM MARKETS"], 3.0, 55, 310, True)
    L.spend("Eating out", ["UBER *EATS", "THE BOATHOUSE", "SUSHI TRAIN", "BREAD AND CIRCUS CAFE", "CHIN CHIN SYDNEY"], 2.5, 18, 165, weekend=1.5)
    L.spend("Transport", ["AMPOL", "BP", "LINKT TOLLS"], 1.3, 30, 130, True)
    L.spend("Shopping", ["DAVID JONES", "THE ICONIC", "AMAZON AU", "MYER", "REBEL SPORT", "OFFICEWORKS"], 1.6, 30, 380, december=1.7)
    L.spend("Kids", ["SMIGGLE", "KMART", "TIMEZONE", "LEGO STORE", "SCHOOL UNIFORM SHOP"], 0.8, 20, 150, december=1.5)
    L.spend("Health", ["MOSMAN PHARMACY", "FAMILY DENTAL CARE", "MEDICAL CENTRE GAP FEE"], 0.4, 25, 180, True)
    L.spend("Entertainment", ["TICKETMASTER", "HOYTS", "DAN MURPHYS", "TARONGA ZOO"], 0.6, 30, 220, weekend=1.6)
    L.save(16, 1500)
    # SHOCK (recurring bill change): variable-rate rise lifts the home loan repayment 11% from July
    L.reprice("HOME LOAN REPAYMENT", date(2026, 7, 1), -7992)


def david(L):  # middle income, smaller mortgage, no kids
    L.every(14, 6, "HARLAN LOGISTICS PAYROLL", 3090, "Income")
    L.monthly(5, "HOME LOAN REPAYMENT", -2650, "Loan repayments")
    L.monthly(12, "PENRITH CITY COUNCIL RATES", -520, "Housing", months=[2, 5, 8, 11])
    L.monthly(21, "ORIGIN ENERGY", -410, "Utilities", jitter=0.18, months=[1, 4, 7, 10])
    L.monthly(25, "SYDNEY WATER", -240, "Utilities", jitter=0.1, months=[3, 6, 9, 12])
    L.monthly(8, "TPG INTERNET", -79.99, "Utilities")
    L.monthly(8, "OPTUS MOBILE", -55, "Utilities")
    L.monthly(2, "HCF HEALTH INSURANCE", -168, "Insurance")
    L.monthly(19, "AAMI HOME AND CAR INSURANCE", -186, "Insurance")
    L.once(6, 17, "SERVICE NSW VEHICLE REGISTRATION", -890, "Transport", True)
    L.monthly(15, "ULTRA TUNE CAR SERVICE", -480, "Transport", months=[4, 10])
    L.monthly(1, "GLENMORE GOLF CLUB MEMBERSHIP", -95, "Entertainment", False)
    for day, name, amt in [(6, "KAYO SPORTS", 25.00), (10, "NETFLIX.COM", 18.99), (14, "SPOTIFY", 13.99)]:
        L.monthly(day, name, -amt, "Subscriptions", False)
    L.spend("Groceries", ["WOOLWORTHS", "COLES", "ALDI STORES"], 2.3, 38, 170, True)
    L.spend("Eating out", ["PENRITH RSL BISTRO", "UBER *EATS", "OPORTO", "THE COFFEE CLUB", "THAI ORCHID"], 2.0, 12, 85, weekend=1.4)
    L.spend("Transport", ["AMPOL", "7-ELEVEN FUEL", "LINKT TOLLS"], 1.1, 25, 115, True)
    L.spend("Shopping", ["BUNNINGS", "JB HI-FI", "BCF", "KMART", "SUPERCHEAP AUTO"], 0.7, 25, 260, december=1.5)
    L.spend("Entertainment", ["DAN MURPHYS", "THE RED COW HOTEL", "GOLF PRO SHOP", "HOYTS"], 0.7, 20, 120, weekend=1.7)
    L.spend("Health", ["CHEMIST WAREHOUSE", "PENRITH MEDICAL CENTRE", "SPECSAVERS"], 0.25, 15, 120, True)
    L.save(18, 500)
    # SHOCK (large single spend): gearbox rebuild at his usual mechanic
    L.add(date(2026, 9, 3), "ULTRA TUNE CAR SERVICE", -3450, "Transport", True)


def margaret(L):  # age pensioner, renting
    def indexed(d):
        return 1.0 + (0.016 if d >= date(2026, 3, 20) else 0) + (0.016 if d >= date(2026, 9, 20) else 0)
    L.every(14, 3, "CENTRELINK AGE PENSION", 1291.40, "Income", scale=indexed)
    L.monthly(15, "AUSTRALIANSUPER PENSION PAYMENT", 250, "Income")
    L.every(14, 4, "RENT RAY WHITE PROPERTY MGMT", -580, "Housing")
    L.monthly(23, "ENERGYAUSTRALIA", -285, "Utilities", jitter=0.2, months=[1, 4, 7, 10])
    L.monthly(7, "TELSTRA MOBILE", -45, "Utilities")
    L.monthly(7, "BELONG INTERNET", -60, "Utilities")
    L.monthly(2, "HCF HEALTH INSURANCE", -142, "Insurance")
    L.monthly(18, "NRMA CONTENTS INSURANCE", -38, "Insurance")
    L.monthly(11, "SALVATION ARMY DONATION", -20, "Donations", False)
    L.every(7, 2, "GOSFORD RSL BINGO", -15, "Entertainment", False)
    L.spend("Groceries", ["WOOLWORTHS", "COLES", "ALDI STORES", "IGA"], 2.6, 22, 98, True)
    L.spend("Health", ["TERRYWHITE CHEMMART", "CHEMIST WAREHOUSE"], 0.8, 7.70, 42, True)
    L.spend("Health", ["GOSFORD FAMILY PRACTICE", "CENTRAL COAST PODIATRY", "OPSM"], 0.2, 30, 95, True)
    L.spend("Eating out", ["THE COFFEE CLUB", "GOSFORD RSL BISTRO", "MUFFIN BREAK", "FISH AND CHIPS CO"], 1.0, 6, 32)
    L.spend("Transport", ["TRANSPORT FOR NSW OPAL", "13CABS"], 0.8, 2.50, 22, True)
    L.spend("Shopping", ["BIG W", "SPOTLIGHT", "MILLERS", "AUSTRALIA POST", "DYMOCKS"], 0.35, 12, 90, december=2.2)
    L.spend("Entertainment", ["EVENT CINEMAS SENIORS", "LINCRAFT", "NEWSAGENCY"], 0.3, 8, 45)
    L.save(21, 150)
    # SHOCK (new merchant, big amount): first visit to a dental implant clinic
    L.add(date(2026, 6, 10), "CENTRAL COAST DENTAL IMPLANTS", -2850, "Health", True)


def jake(L):  # sole-trader electrician with an ABN, share house
    clients = ["HARBOUR BUILD GROUP", "INNER WEST RENOVATIONS", "CASTLE STRATA SERVICES", "M AND K CONSTRUCTIONS",
               "GREENLEAF CAFE FITOUT", "PRIVATE CLIENT"]
    inv, (y, m) = 1040, (START.year, START.month)
    while date(y, m, 1) <= END:
        for _ in range(L.rng.randint(4, 6)):
            inv += 1
            L.add(date(y, m, L.rng.randint(1, 28)), f"INV {inv} {L.rng.choice(clients)}", L.rng.triangular(1200, 3200, 2100), "Income")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    L.every(7, 1, "RENT SHARE HOUSE MARRICKVILLE", -310, "Housing")
    L.monthly(12, "TOYOTA FINANCE HILUX", -640, "Loan repayments")
    L.monthly(14, "SPLIT BILLS POWER AND INTERNET", -85, "Utilities", jitter=0.2)
    L.monthly(9, "TELSTRA MOBILE", -69, "Utilities")
    L.monthly(3, "QBE PUBLIC LIABILITY INSURANCE", -92, "Insurance")
    L.monthly(3, "XERO SUBSCRIPTION", -35, "Business expenses")
    L.monthly(28, "ATO BAS PAYMENT", -4800, "Tax", jitter=0.13, months=[2, 4, 7, 10])
    L.once(9, 8, "SERVICE NSW VEHICLE REGISTRATION", -980, "Transport", True)
    L.once(5, 19, "NSW FAIR TRADING LICENCE RENEWAL", -260, "Business expenses", True)
    L.monthly(1, "ANYTIME FITNESS", -64, "Health", False)
    for day, name, amt in [(6, "KAYO SPORTS", 25.00), (14, "SPOTIFY", 13.99)]:
        L.monthly(day, name, -amt, "Subscriptions", False)
    L.once(5, 2, "JETSTAR AIRWAYS", -720, "Travel")
    L.once(5, 6, "BOOKING.COM BALI VILLA", -880, "Travel")
    L.spend("Transport", ["AMPOL", "BP", "7-ELEVEN FUEL"], 1.8, 88, 150, True)
    L.spend("Business expenses", ["MIDDYS ELECTRICAL", "REXEL ELECTRICAL", "BUNNINGS TRADE", "SYDNEY TOOLS", "TOTAL TOOLS"], 1.7, 35, 480, True)
    L.spend("Groceries", ["WOOLWORTHS", "COLES", "ALDI STORES"], 1.6, 28, 120, True)
    L.spend("Eating out", ["UBER *EATS", "BAKERS DELIGHT", "ROSIES CAFE", "EL JANNAH", "MCDONALDS", "VIC ON THE PARK"], 4.0, 8, 48, weekend=1.3)
    L.spend("Entertainment", ["THE HENSON", "DAN MURPHYS", "TICKETEK", "HOLEY MOLEY"], 1.4, 18, 140, weekend=1.8)
    L.spend("Shopping", ["REBEL SPORT", "JB HI-FI", "BCF", "KATHMANDU", "AMAZON AU"], 0.6, 30, 240, december=1.4)
    L.spend("Health", ["CHEMIST WAREHOUSE", "MARRICKVILLE PHYSIO"], 0.15, 12, 95, True)
    L.save(25, 1000, "TRANSFER TO ESAVER TAX SET-ASIDE")
    # SHOCK (category spike): tools and gear for a big commercial job, spread over one week
    for day, desc, amt in [(10, "SYDNEY TOOLS", 445), (11, "MIDDYS ELECTRICAL", 430), (12, "TOTAL TOOLS", 460),
                           (13, "REXEL ELECTRICAL", 415), (14, "BUNNINGS TRADE", 390)]:
        L.add(date(2026, 8, day), desc, -amt, "Business expenses", True)


USERS = [
    dict(user_id="u001", username="mia.tran", password="Student#2026", first_name="Mia", last_name="Tran", age=20,
         persona="University student with a part-time cafe job", employment="Casual, part-time", household="Share house, renting a room",
         build=mia, open_everyday=850, open_savings=1400, savings_rate=0.045, card="4471"),
    dict(user_id="u002", username="priya.sharma", password="Clinic#2026", first_name="Priya", last_name="Sharma", age=44,
         persona="High-income business owner with a home loan, a business loan and two children", employment="Director, own physiotherapy practice",
         household="Home owner, two children (9 and 12)", build=priya, open_everyday=14500, open_savings=42000, savings_rate=0.045, card="8820",
         loans=[("homeloan", "Rocket Repay Home Loan", -1148300.00), ("bizloan", "Business Loan", -176450.00)], property_value=2150000),
    dict(user_id="u003", username="david.kowalski", password="Fairway#2026", first_name="David", last_name="Kowalski", age=47,
         persona="Middle-income earner with a modest mortgage and no children", employment="Full-time operations coordinator",
         household="Home owner, lives alone", build=david, open_everyday=4200, open_savings=18500, savings_rate=0.045, card="3056",
         loans=[("homeloan", "Rocket Repay Home Loan", -402700.00)], property_value=780000),
    dict(user_id="u004", username="margaret.obrien", password="Garden#2026", first_name="Margaret", last_name="O'Brien", age=72,
         persona="Age pensioner who rents and owns no property", employment="Retired, Age Pension plus a small super pension",
         household="Renting a unit, lives alone", build=margaret, open_everyday=2100, open_savings=14800, savings_rate=0.045, card="1294"),
    dict(user_id="u005", username="jake.morrison", password="Sparky#2026", first_name="Jake", last_name="Morrison", age=29,
         persona="Self-employed electrician with an ABN, living in a share house", employment="Sole trader, irregular invoice income",
         household="Share house, renting a room", build=jake, open_everyday=6800, open_savings=11200, savings_rate=0.045, card="6637"),
]


def run():
    all_tx, users_out, front = [], [], []
    (HERE / "statements").mkdir(exist_ok=True)
    for i, u in enumerate(USERS):
        L = Ledger(u["user_id"], random.Random(SEED + i))
        u["build"](L)
        # monthly interest on savings
        rows = sorted(L.rows, key=lambda r: (r["date"], r["amount"] < 0))
        bal = {"everyday": 0.0, "savings": float(u["open_savings"])}
        low = 0.0
        for r in rows:  # find the lowest point so the everyday account never goes negative
            if r["account"] == "everyday":
                bal["everyday"] += r["amount"]
                low = min(low, bal["everyday"])
        bal["everyday"] = max(float(u["open_everyday"]), 250 - low)
        opening = dict(bal)
        out, month = [], START.month
        for r in rows:
            if r["date"].month != month:  # interest on the 1st of each new month
                month = r["date"].month
                interest = round(bal["savings"] * u["savings_rate"] / 12, 2)
                bal["savings"] += interest
                out.append(dict(date=r["date"].replace(day=1), description="INTEREST PAID", amount=interest, category="Income",
                                type="income", essential=False, recurring=True, account="savings", balance_after=round(bal["savings"], 2)))
            bal[r["account"]] += r["amount"]
            out.append(dict(r, balance_after=round(bal[r["account"]], 2)))
        for n, r in enumerate(out, 1):
            r.update(transaction_id=f"{u['user_id']}-{n:05d}", user_id=u["user_id"],
                     account_id=f"{u['user_id']}-{r.pop('account')}", date=r["date"].isoformat())
        all_tx += out

        accounts = [
            dict(account_id=f"{u['user_id']}-everyday", name="Westpac Choice", type="transaction", opening_balance=round(opening["everyday"], 2),
                 balance=round(bal["everyday"], 2), card_ending=u["card"]),
            dict(account_id=f"{u['user_id']}-savings", name="Westpac eSaver", type="savings", opening_balance=round(opening["savings"], 2),
                 balance=round(bal["savings"], 2)),
        ] + [dict(account_id=f"{u['user_id']}-{k}", name=n, type="loan", balance=b) for k, n, b in u.get("loans", [])]
        profile = {k: u[k] for k in ("user_id", "username", "password", "first_name", "last_name", "age", "persona", "employment", "household")}
        users_out.append(dict(profile, property_value=u.get("property_value", 0), accounts=accounts))

        with open(HERE / "statements" / f"{u['username']}.csv", "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["Bank Account", "Date", "Narrative", "Debit Amount", "Credit Amount", "Balance", "Categories"])
            for r in out:
                d = date.fromisoformat(r["date"]).strftime("%d/%m/%Y")
                w.writerow([r["account_id"], d, r["description"], f"{-r['amount']:.2f}" if r["amount"] < 0 else "",
                            f"{r['amount']:.2f}" if r["amount"] > 0 else "", f"{r['balance_after']:.2f}", r["category"]])

        # summary for the app shell: this month so far, excluding transfers between own accounts
        mtd = [r for r in out if r["date"] >= END.replace(day=1).isoformat() and r["type"] != "transfer"]
        spent = -sum(r["amount"] for r in mtd if r["amount"] < 0)
        earned = sum(r["amount"] for r in mtd if r["amount"] > 0)
        cash = bal["everyday"] + bal["savings"]
        debt = -sum(b for _, _, b in u.get("loans", []))
        recent = [r for r in out if r["account_id"].endswith("everyday")][-12:][::-1]
        front.append(dict(
            id=u["user_id"], username=u["username"], password=u["password"], firstName=u["first_name"], persona=u["persona"],
            accounts=[dict(id=a["account_id"], name=a["name"], balance=a["balance"], kind="loan" if a["type"] == "loan" else "cash",
                           cardEnding=a.get("card_ending")) for a in accounts],
            cashFlow=dict(since=END.replace(day=1).strftime("%d %b"), expenses=round(spent, 2), income=round(earned, 2)),
            categoriesTotal=round(spent, 2),
            netWorth=dict(assets=round(cash + u.get("property_value", 0), 2), liabilities=round(debt, 2)),
            recent=[dict(date=r["date"], description=r["description"], amount=r["amount"]) for r in recent]))

        inc = sum(r["amount"] for r in out if r["type"] == "income"); exp = -sum(r["amount"] for r in out if r["type"] == "expense")
        ev = [r["balance_after"] for r in out if r["account_id"].endswith("everyday")]
        print(f"{u['username']:16} tx={len(out):5} income={inc:10,.0f} spend={exp:10,.0f} everyday open={opening['everyday']:9,.0f} "
              f"min={min(ev):8,.0f} end={bal['everyday']:9,.0f} savings end={bal['savings']:9,.0f}")

    (HERE / "mock_users.json").write_text(json.dumps(users_out, indent=2))
    (HERE / "mock_transactions.json").write_text(json.dumps(all_tx, indent=1))
    fe = HERE / "../../frontend/src/data"
    if fe.exists():
        (fe / "mockUsers.json").write_text(json.dumps(front, indent=1))
    print("total transactions:", len(all_tx))


if __name__ == "__main__":
    run()
