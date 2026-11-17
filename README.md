# WestPlan — AI-Powered Financial Goal Planning

**Adaptive financial planning that responds to life's unexpected expenses.**

WestPlan is a hackathon prototype that explores how AI can help customers maintain achievable financial goals when their circumstances change.

Traditional financial planning tools often rely on static savings targets and contribution schedules. When an unexpected expense occurs, customers may need to manually reassess their budgets and adjust their goals.

WestPlan introduces an adaptive planning approach that combines financial event detection, AI-powered replanning, deterministic financial calculations, independent safety assessments and human approval.

The solution is designed to extend existing banking infrastructure, using simulated financial data to demonstrate how an intelligent financial planning assistant could operate within a banking environment.

## The Problem

Financial circumstances rarely remain constant.

Unexpected expenses, such as vehicle repairs, veterinary bills or emergency household costs, can disrupt carefully planned savings goals. Customers may find themselves choosing between maintaining their savings contributions and covering immediate financial needs.

Existing financial planning tools can help users establish goals, but may offer limited support when those goals become difficult to achieve.

WestPlan explores how financial planning can become more responsive, helping customers adjust their contributions while maintaining realistic long-term objectives.

## Our Solution

WestPlan connects financial context, unexpected events and AI-assisted planning into a single system.

When a financial shock is identified, the platform evaluates its impact on the customer's goals and generates potential adjustments.

Each proposal is assessed against financial constraints before being presented to the customer for review.

The customer retains control over the final decision.

### Core Capabilities

- **Financial goal management:** Create and manage savings goals, including target amounts, deadlines and contribution schedules.
- **Financial shock simulation:** Trigger unexpected expenses to demonstrate how financial circumstances affect existing plans.
- **AI-powered replanning:** Generate potential adjustments using LLM-based reasoning and financial calculation tools.
- **Deterministic financial calculations:** Calculate projections, contribution schedules and goal feasibility using Python.
- **Independent AI safety assessment:** Evaluate proposed adjustments against financial constraints and risks.
- **Automatic revisions:** Allow the planner to correct or reconsider proposals when validation or safety checks identify problems.
- **Human-in-the-loop decisions:** Present proposals for customer approval or rejection.
- **Persistent financial context:** Store goals, events, proposals, decisions and planning history.
- **Multi-customer demonstration:** Explore planning scenarios using synthetic customer profiles.

## System Architecture

WestPlan uses a modular architecture separating financial data, application services, AI reasoning, deterministic calculations and safety assessment.

| Component | Technology / Responsibility |
|---|---|
| Frontend | React, Vite |
| Backend API | Python, FastAPI |
| Financial Data Layer | Synthetic banking and transaction data |
| Shock Detector | Financial event identification and simulation |
| AI Planning Orchestrator | Planning coordination, model interactions and revisions |
| LLM Providers | OpenRouter and Gemini integrations |
| Financial Calculation Engine | Deterministic Python calculations |
| AI Safety Checker | Independent proposal safety assessment |
| Revision & Recovery | Retries, corrections and resumable planning |
| Data Storage | SQLite |
| Notifications & Approval | Customer alerts and proposal review |

### Architecture Overview

The prototype is designed around an assumed Westpac financial infrastructure.

The financial data layer supplies customer balances, transactions, income and expenses. The shock detection component identifies events that may affect a customer's financial goals.

The backend manages customer context and connects financial information to the AI planning system.

The AI Planning Orchestrator coordinates language models and financial calculation tools to generate proposed adjustments. These proposals are evaluated by the AI Safety Checker, which can request revisions or prevent unsafe recommendations from progressing.

Approved adjustments can be persisted to the customer's goal schedule, while rejected proposals remain unapplied.

**Important:** The Westpac Financial Engine represents assumed external infrastructure. The hackathon implementation uses synthetic financial information and manually triggered events rather than a live Westpac banking integration.

## AI Planning Engine

The AI Planning Orchestrator is responsible for coordinating the replanning process.

It combines:

- Customer financial context and existing savings goals.
- Information about unexpected financial events.
- LLM-generated planning recommendations.
- Python-based financial projections and feasibility calculations.
- Independent safety feedback.
- Revision and recovery mechanisms.

### LLM Integration

The system supports configurable model providers, including:

- **OpenRouter**, supporting Nemotron and other compatible models.
- **Google Gemini**, through a separate provider integration.

A provider abstraction allows planning components to interact with different model services without embedding provider-specific logic throughout the application.

Model availability, response quality and tool-calling behaviour can influence planning performance.

### Deterministic Financial Calculation Engine

Financial calculations are performed using Python tools rather than relying exclusively on the LLM's arithmetic.

These tools support financial projections, contribution calculations and feasibility assessments.

Separating financial calculations from language-model reasoning improves consistency and makes proposals easier to assess.

## AI Safety Checker

Financial safety is a core design consideration.

WestPlan separates proposal generation from safety assessment so that recommendations are independently evaluated before they become eligible for customer approval.

The checker evaluates proposals against defined financial constraints and the available financial context, including affordability, feasibility and potential financial risks.

### Safety Outcomes

| Decision | Meaning |
|---|---|
| **PASS** | The proposal satisfies the implemented safety checks and can proceed to customer review. |
| **REVISE** | The proposal requires correction or further evaluation. |
| **BLOCK** | The proposal is not eligible for approval. |

The system supports iterative revisions when proposals are unsuitable.

The planner can revisit its calculations and recommendations using validation and safety feedback.

A proposal that remains blocked or requires unresolved revisions cannot be approved.

### Human Oversight

Passing the safety assessment does not automatically activate a financial plan.

The customer must explicitly decide whether to accept or reject a proposed adjustment.

This ensures that AI-generated recommendations remain subject to human review.

The safety checks are prototype safeguards, not a guarantee that every financially relevant risk has been evaluated.

## Revision and Recovery

Generating financially feasible plans can require multiple attempts, particularly when using smaller or less capable language models.

The planning system includes mechanisms for:

- Automatic proposal corrections.
- Safety-driven revisions.
- Validation and recalculation.
- API rate-limit handling.
- Model request retries.
- Resumable planning checkpoints.

These features help the system recover from interrupted model interactions and revise recommendations that fail validation or safety checks.

## Demonstration Workflow

The hackathon demo illustrates how WestPlan responds to an unexpected financial expense.

### 1. Create a Financial Goal

A customer creates a savings goal with a target amount and deadline.

The goal is stored in the backend and becomes part of the customer's financial planning context.

### 2. Trigger an Unexpected Expense

A financial shock is simulated, such as an unexpected vehicle repair or veterinary bill.

The system records the event and updates the relevant financial context.

For the hackathon demonstration, events are triggered manually.

### 3. Generate an Adjusted Plan

The AI Planning Orchestrator receives the financial context and evaluates how the event affects the customer's goal.

It uses financial calculation tools to explore revised contribution schedules and other potential adjustments.

### 4. Evaluate Financial Safety

The AI Safety Checker assesses the proposed plan.

If the proposal requires changes, feedback is returned to the planner for revision.

Proposals that fail critical safety constraints are blocked.

### 5. Notify the Customer

Once an eligible proposal is available, the application can present an AI-generated summary explaining the financial impact and recommended adjustments.

### 6. Approve or Reject

The customer reviews the proposed adjustment and chooses whether to accept or reject it.

An approved schedule can be applied and stored in the backend. A rejected proposal does not activate the proposed changes.

## Technology Stack

### Frontend
- React
- JavaScript / JSX
- Vite

### Backend
- Python
- FastAPI
- Uvicorn
- SQLite

### AI and Planning
- OpenRouter API
- Google Gemini integration
- Configurable LLM providers
- Deterministic Python financial tools
- Planning orchestration and safety assessment

### Testing and Demonstration
- Synthetic customer profiles
- Mock banking transactions
- Manually triggered financial events
- Backend API testing
- Real-model integration tests

## Getting Started

### Prerequisites

- Python 3.11 or a compatible version
- Node.js and npm
- An API key for the configured LLM provider

### 1. Clone the Repository

```bash
git clone https://github.com/zeledorka/westplan.git
cd westplan
```

The application is located in the `westpac-goal-planner 2` directory.

### 2. Set Up the Backend

```bash
cd "westpac-goal-planner 2/backend"
```

Create and activate a virtual environment if one is not already configured:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the backend dependencies using the dependency specification provided in the repository.

Configure the required model-provider credentials locally. Do not commit API keys or `.env` files.

For example, to select OpenRouter as the provider:

```bash
export PLANNER_PROVIDER=openrouter
export OPENROUTER_MODEL="nvidia/nemotron-3-ultra-550b-a55b:free"
export OPENROUTER_PROFILE=tools_json_text
export OPENROUTER_ALLOW_PAID=false
```

Ensure your OpenRouter credentials are available to the backend.

Start the API:

```bash
python -m uvicorn api:app --host 127.0.0.1 --port 8012
```

The API documentation is available at:

`http://127.0.0.1:8012/docs`

### 3. Start the Frontend

Open another terminal:

```bash
cd "westpac-goal-planner 2/frontend"
npm install
```

Start the frontend with the local backend:

```bash
WESTPLAN_API_URL=http://127.0.0.1:8012 \
npm run dev -- --host 127.0.0.1 --port 5181
```

Open:

`http://127.0.0.1:5181/#demo`

The frontend and backend must both be running for the integrated demonstration.

### 4. Explore the Demo

Select a synthetic customer, create or inspect a goal and use the integrated demo controls to trigger a financial event.

Run the AI assessment to generate and evaluate potential adjustments.

Where supported by the active demo configuration, review the resulting proposal and use the approval or rejection controls.

## Testing

The project includes Python-based financial calculation and shock detection tests, alongside integration testing of the AI planning workflow.

The backend contains financial statistics and shock calculation modules under:

```text
backend/finstats/
```

Financial calculation tests are available under:

```text
backend/tests/
```

### Verified Integration Behaviour

During hackathon development, integration tests demonstrated:

- Successful generation of financial proposals using real OpenRouter requests.
- Independent safety evaluation returning PASS and BLOCK outcomes.
- Financial shock processing and updated financial projections.
- Backend application of a passing contribution schedule.
- Persistence of approval information and updated goal data in SQLite.
- Frontend communication with the backend for customer selection, goal creation and event submission.

A successful sandbox approval test applied a $600 monthly contribution schedule with no contribution pause and no deadline change.

The browser-based approval flow was not independently verified end to end in that same test.

## Project Structure

```text
westplan/
├── westpac-goal-planner 2/
│   ├── backend/
│   │   ├── api.py
│   │   ├── data/
│   │   │   ├── mock_users.json
│   │   │   ├── mock_transactions.json
│   │   │   └── statements/
│   │   ├── finstats/
│   │   ├── planning/
│   │   └── tests/
│   │
│   ├── frontend/
│   │   ├── src/
│   │   │   ├── components/
│   │   │   ├── pages/
│   │   │   └── data/
│   │   └── package.json
│   │
│   └── README.md
│
└── README.md
```

*This is a simplified structural overview of the components discussed during development; the exact repository tree may differ.*

## Current Limitations

WestPlan is a functional hackathon prototype, not a production banking platform.

Current limitations include:

- **Simulated banking infrastructure:** Financial data comes from synthetic datasets rather than live Westpac banking services.
- **Manual event triggering:** Financial shocks are manually simulated for demonstration purposes.
- **Model variability:** Planning quality and completion time depend on the configured LLM.
- **Iterative planning overhead:** Some scenarios require multiple corrections or safety revisions.
- **Limited end-to-end verification:** Some capabilities have been tested at the backend level without complete browser-level coverage.
- **Prototype safety coverage:** The safety checker evaluates defined constraints but does not constitute comprehensive financial advice validation.
- **No production banking integration:** Authentication, authorisation and approval workflows require additional production-grade integration and assurance.

## Future Development

Potential future improvements include:

- Integration with live banking and transaction infrastructure.
- Automated financial shock detection.
- More reliable planning with stronger language models.
- Reduced model calls and more efficient revision strategies.
- Additional financial safety checks and personalised risk constraints.
- More comprehensive end-to-end testing.
- Improved transparency and explanations for AI recommendations.
- Production-ready customer approval and notification integration.

## Responsible AI

WestPlan is built around the principle that AI should assist financial decision-making rather than independently control financial outcomes.

The architecture therefore emphasises deterministic financial calculations, independent safety assessment, explicit human approval and traceable planning decisions.

AI-generated proposals are recommendations, not automatically authorised financial actions.

## Disclaimer

WestPlan is a hackathon prototype developed for demonstration and research purposes.

It is not an official Westpac product, is not connected to live Westpac customer accounts and does not provide licensed financial advice.

All demonstration customer profiles and financial transactions are synthetic. Financial recommendations generated by the prototype should not be relied upon for real financial decisions without appropriate professional review.
