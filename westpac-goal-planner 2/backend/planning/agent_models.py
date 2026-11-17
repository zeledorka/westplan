"""Untrusted model contracts. IDs, versions and computed figures are server-owned."""
from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator, AfterValidator

from .schemas import money

Amount = Annotated[float, Field(ge=0, le=100_000_000, allow_inf_nan=False, strict=True), AfterValidator(money)]
Text = Annotated[str, Field(min_length=1, max_length=2400)]

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid')

class GoalPlan(Contract):
    goal_ref: str = Field(min_length=1, max_length=32)
    contribution: Amount
    frequency: Literal['monthly','fortnightly'] = 'monthly'
    pause_months: Annotated[StrictInt, Field(ge=0, le=24)] = 0
    target: Amount | None = None
    deadline: date | None = None
    priority: Annotated[StrictInt, Field(ge=1, le=5)] | None = None

class CalculationRequest(Contract):
    plans: list[GoalPlan] = Field(min_length=1, max_length=8)
    hypothetical_one_off_expense: Amount = 0
    hypothetical_missing_income: Amount = 0

class Strategy(Contract):
    calculation_ref: str
    title: Annotated[str, Field(min_length=1,max_length=120)]
    approach: Text
    proposed_actions: list[Text] = Field(max_length=8)
    rationale: Text
    benefits: list[Text] = Field(max_length=6)
    drawbacks: list[Text] = Field(max_length=6)
    assumptions: list[Text] = Field(max_length=8)

class PlannerResult(Contract):
    objective: Text
    answer: Text
    assessment: Literal['feasible','constrained','not_achievable','needs_clarification']
    change_recommended: StrictBool
    recommended_ref: str | None
    strategies: list[Strategy] = Field(max_length=4)
    assumptions: list[Text] = Field(max_length=8)
    missing_information: list[Text] = Field(max_length=8)
    shock_refs: list[str] = Field(max_length=32)

    @model_validator(mode='after')
    def references(self):
        refs=[s.calculation_ref for s in self.strategies]
        if len(refs)!=len(set(refs)) or self.recommended_ref is not None and self.recommended_ref not in refs:
            raise ValueError('Recommendation must refer to one distinct calculated strategy')
        if self.change_recommended and self.recommended_ref is None:
            raise ValueError('A recommended change requires a calculated strategy')
        if self.assessment=='needs_clarification' and not self.missing_information:
            raise ValueError('Clarification requires a question')
        return self
