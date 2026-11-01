"""
Budget Agent — sums the cost of every parent task in the trip currency
(converting via Frankfurter rates when a parent priced in another currency)
and checks the total against the budget.
"""

from __future__ import annotations

from typing import Any

from app.agents.base import AgentResult, BaseAgent
from app.agents.common import currency_of
from app.agents.validation import finish
from app.core.contracts import evaluate_contract
from app.core.models import NodeType, TaskNode
from app.integrations.http import ProviderError


class BudgetAgent(BaseAgent):
    def __init__(self, providers: Any) -> None:
        self.p = providers

    async def run(self, node: TaskNode, resolved_inputs: dict[str, Any], frozen_context: dict[str, Any]) -> AgentResult:
        ri = resolved_inputs
        currency = currency_of(ri)
        try:
            limit = float(ri["budget_limit"])
        except (KeyError, TypeError, ValueError):
            return AgentResult.fail("budget_limit missing or invalid", retryable=False)

        line_items: dict[str, float] = {}
        breakdown: dict[str, float] = {}
        converted: list[str] = []
        try:
            for pid, parent in sorted((ri.get("_parents") or {}).items()):
                out = parent.get("output") or {}
                cost = out.get("cost")
                if not isinstance(cost, (int, float)):
                    continue
                item_ccy = str(out.get("currency", currency)).upper()
                if item_ccy != currency:
                    cost = await self.p.fx.convert(float(cost), item_ccy, currency)
                    converted.append(pid)
                line_items[pid] = round(float(cost), 2)
                key = parent.get("type", "OTHER").lower()
                breakdown[key] = round(breakdown.get(key, 0.0) + float(cost), 2)
            rates = await self.p.fx.rates()
        except ProviderError as exc:
            return AgentResult.fail(f"currency conversion failed: {exc}")

        total = round(sum(line_items.values()), 2)
        check = evaluate_contract("BUDGET_WITHIN_LIMIT", {"total_cost": total, "budget_limit": limit,
                                                          "currency": currency})
        return finish(NodeType.BUDGET, {
            "budget_limit": limit,
            "total_cost": total,
            "remaining": round(limit - total, 2),
            "within_budget": total <= limit,
            "currency": currency,
            "line_items": line_items,
            "breakdown": breakdown,
            "explanation": check.explanation,
            "converted_items": converted,
            "fx_date": rates.date,
            "fx_source": rates.source,
        })
