"""Provider-independent V1 policy for SOOI Search Credits.

These are SOOI commercial units, not tokens, model prices, euros, or provider costs.
SR0.3C deliberately maps them one-to-one to the customer's finite authorized maximum;
commercial economics can be calibrated later without changing technical planner caps.
"""

from decimal import Decimal


def reservation_credits(search_mode, authorized_max):
    amount = Decimal(str(authorized_max))
    if search_mode == "free":
        return Decimal("0")
    return amount


def billable_consumption(search_run):
    """Actual executed external discovery units already measured by the planner."""
    return Decimal(str(search_run.budget_consumed_credits or 0))
