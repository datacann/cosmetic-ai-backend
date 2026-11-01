from dataclasses import dataclass


@dataclass(frozen=True)
class PlanEntitlements:
    plan: str
    analysis_limit: int
    period: str
    history_limit: int | None
    ads_enabled: bool
    rewarded_daily_limit: int
    premium_entitled: bool


PLANS = {
    "guest": PlanEntitlements("guest", 1, "monthly", 0, True, 0, False),
    "free": PlanEntitlements("free", 5, "monthly", 10, True, 2, False),
    "premium": PlanEntitlements("premium", 100, "monthly", None, False, 0, True),
}


def entitlements_for(plan: str | None) -> PlanEntitlements:
    return PLANS.get(plan or "free", PLANS["free"])
