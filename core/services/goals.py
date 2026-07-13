"""Servicios de lógica de negocio para el módulo de metas (Goal).

La lógica es la inversión de la de deudas: en una meta, un `income`
representa un aporte al ahorro (aumenta lo abonado) y un `expense`
representa retirar dinero de la meta (disminuye lo abonado).
"""

from decimal import Decimal

from django.core.exceptions import ValidationError


def apply_transaction_to_goal(transaction):
    """Actualiza el monto abonado de la meta asociada a una transacción recién guardada.

    - income → aumenta monto_abonado (aporte al ahorro).
    - expense → disminuye monto_abonado (retiro de la meta).
    - transfer → no-op.
    """
    goal = transaction.goal
    if goal is None:
        return

    if transaction.transaction_type == "income":
        goal.monto_abonado += transaction.amount
    elif transaction.transaction_type == "expense":
        goal.monto_abonado -= transaction.amount
        goal.monto_abonado = max(goal.monto_abonado, Decimal("0.00"))
    else:
        return

    goal.save(update_fields=["monto_abonado", "updated_at"])


def revert_transaction_from_goal(transaction):
    """Revierte el efecto de una transacción sobre su meta (para update/delete)."""
    goal = transaction.goal
    if goal is None:
        return

    if transaction.transaction_type == "income":
        goal.monto_abonado -= transaction.amount
        goal.monto_abonado = max(goal.monto_abonado, Decimal("0.00"))
    elif transaction.transaction_type == "expense":
        goal.monto_abonado += transaction.amount
    else:
        return

    goal.save(update_fields=["monto_abonado", "updated_at"])


def validate_income_against_goal(goal, amount):
    """Lanza ValidationError si el aporte haría superar el objetivo de la meta."""
    if goal.monto_abonado + amount > goal.monto_requerido:
        raise ValidationError(
            f"El aporte (${amount:,.2f}) supera lo que falta para completar la "
            f"meta. Máximo abonable: ${goal.monto_pendiente:,.2f}."
        )


def validate_expense_against_goal(goal, amount):
    """Lanza ValidationError si el retiro dejaría el monto abonado en negativo."""
    if amount > goal.monto_abonado:
        raise ValidationError(
            f"El retiro (${amount:,.2f}) supera el monto abonado a la meta "
            f"(${goal.monto_abonado:,.2f})."
        )


def get_goal_transaction_history(goal):
    """Retorna las transacciones asociadas a una meta, ordenadas por fecha."""
    return goal.transactions.select_related("account", "category").order_by("-date")
