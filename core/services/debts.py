"""Servicios de lógica de negocio para el módulo de deudas."""

from decimal import Decimal

from django.core.exceptions import ValidationError


def apply_transaction_to_debt(transaction):
    """Actualiza los montos de la deuda asociada a una transacción recién creada/guardada.

    - income → aumenta monto_requerido (el usuario recibió más dinero prestado).
    - expense → aumenta monto_pagado (el usuario abonó a la deuda).
    - transfer → no-op.
    """
    debt = transaction.debt
    if debt is None:
        return

    if transaction.transaction_type == "income":
        debt.monto_requerido += transaction.amount
    elif transaction.transaction_type == "expense":
        debt.monto_pagado += transaction.amount
    else:
        return

    debt.save(update_fields=["monto_requerido", "monto_pagado", "updated_at"])


def revert_transaction_from_debt(transaction):
    """Revierte el efecto de una transacción sobre su deuda (para update/delete)."""
    debt = transaction.debt
    if debt is None:
        return

    if transaction.transaction_type == "income":
        debt.monto_requerido -= transaction.amount
        debt.monto_requerido = max(debt.monto_requerido, Decimal("0.00"))
    elif transaction.transaction_type == "expense":
        debt.monto_pagado -= transaction.amount
        debt.monto_pagado = max(debt.monto_pagado, Decimal("0.00"))
    else:
        return

    debt.save(update_fields=["monto_requerido", "monto_pagado", "updated_at"])


def validate_expense_against_debt(debt, amount):
    """Lanza ValidationError si el monto del gasto supera el saldo pendiente de la deuda."""
    if amount > debt.monto_pendiente:
        raise ValidationError(
            f"El monto del gasto (${amount:,.2f}) supera el saldo pendiente "
            f"de la deuda (${debt.monto_pendiente:,.2f})."
        )


def get_debt_transaction_history(debt):
    """Retorna el queryset de transacciones asociadas a una deuda, ordenadas por fecha."""
    return debt.transactions.select_related("account", "category").order_by("-date")
