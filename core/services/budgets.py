"""Cálculo del gasto real de un presupuesto en su categoría y periodo."""

from decimal import Decimal

from django.db.models import Sum

from core.models import Transaction


def calculate_budget_spent(budget):
    """Suma las transacciones del usuario en la categoría del presupuesto dentro
    de su periodo, del mismo tipo que la categoría (gasto acumula gastos,
    ingreso acumula ingresos)."""
    total = Transaction.objects.filter(
        user=budget.user,
        category=budget.category,
        transaction_type=budget.category.category_type,
        date__gte=budget.period_start,
        date__lte=budget.period_end,
    ).aggregate(total=Sum("amount"))["total"]

    return total or Decimal("0")
