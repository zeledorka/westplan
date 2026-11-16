# Goal Planner hackathon prototype

## frontend/

A React + Vite mock-up of the banking app shell: Home, Accounts, Payments, Insights and More,
plus a placeholder Goal planner screen (opened from the "Goal planner" card on Home or Insights).

Buttons are for show; only the bottom tabs and the Goal planner entry navigate.
All figures are synthetic and live in `src/data/mockBank.js`.

### Run it

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 in a browser.

### View it in the Android emulator

1. Start an emulator from Android Studio (Device Manager).
2. Keep `npm run dev` running on your computer.
3. In the emulator, open Chrome and go to http://10.0.2.2:5173
   (10.0.2.2 is the emulator's address for your computer).
4. For a full-screen look, use Chrome's menu, then "Add to Home screen".

On a real Android phone on the same Wi-Fi, use the "Network" address that `npm run dev` prints.

### Where things are

- `src/pages/` one file per screen
- `src/components/ui.jsx` shared pieces (tab bar, cards, tiles)
- `src/components/Icon.jsx` icon set and the logo stand-in
- `src/styles.css` all styling; brand colours are the variables at the top
- `src/pages/GoalPlanner.jsx` where the team's feature mounts

Fictional data only. This is a hackathon prototype, not a Westpac product.

## backend/data/ (synthetic customers)

Five fictional customers, each with one year of transactions (16 Nov 2025 to 15 Nov 2026).

- `mock_users.json` profiles, demo logins and accounts
- `mock_transactions.json` every transaction, flat list with `user_id`, `account_id`, `category`, `type`, `essential`, `recurring`, `balance_after`
- `statements/<username>.csv` the same data as one CSV statement per customer
- `generate_mock_data.py` regenerates all of the above and `frontend/src/data/mockUsers.json`

```bash
cd backend/data
python generate_mock_data.py
```

| Username | Password | Who |
|---|---|---|
| mia.tran | Student#2026 | Student, 20, part-time cafe job, share house |
| priya.sharma | Clinic#2026 | Business owner, 44, home loan + business loan, two kids |
| david.kowalski | Fairway#2026 | Middle income, 47, smaller mortgage, no kids |
| margaret.obrien | Garden#2026 | Age pensioner, 72, renting, no property |
| jake.morrison | Sparky#2026 | Electrician with an ABN, 29, share house |

The sign-in screen is a demo-only plain-text check against these mock users. Tap a user on that screen to fill in their details. The profile icon signs out.
