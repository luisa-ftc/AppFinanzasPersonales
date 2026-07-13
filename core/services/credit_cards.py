"""Cálculos derivados de cuentas de tipo tarjeta de crédito (no persistidos)."""

import calendar
from datetime import date
from decimal import Decimal

from django.db.models import Sum


def get_used_credit(account):
    """Crédito utilizado = deuda actual de la tarjeta.

    No reutiliza `calculate_account_balance`: esa función trata la cuenta
    como una billetera (los gastos *restan* saldo, como en una cuenta
    corriente), mientras que en una tarjeta de crédito un gasto *aumenta*
    la deuda y un pago (transferencia entrante desde otra cuenta) la
    *reduce* — es la relación opuesta. Por eso se calcula aquí de forma
    independiente, con los signos invertidos respecto al saldo genérico.
    Se acota a no negativo de cara al usuario.
    """
    expense = account.transactions.filter(
        transaction_type="expense"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    income = account.transactions.filter(
        transaction_type="income"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    transfers_out = account.transactions.filter(
        transaction_type="transfer"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    payments_in = account.incoming_transfers.filter(
        transaction_type="transfer"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    debt = account.initial_balance + expense - income + transfers_out - payments_in
    return max(debt, Decimal("0.00"))


def get_available_credit(account, details):
    """Crédito disponible = cupo - crédito utilizado, acotado a no negativo."""
    available = details.credit_limit - get_used_credit(account)
    return max(available, Decimal("0.00"))


def _next_occurrence_of_day(day, today=None):
    """Devuelve la próxima fecha (hoy o futura) cuyo día del mes sea `day`.

    Si el mes objetivo tiene menos días que `day` (ej. día 31 en febrero), se
    usa el último día real de ese mes en vez de lanzar un error de fecha
    inválida.
    """
    today = today or date.today()
    last_day_this_month = calendar.monthrange(today.year, today.month)[1]
    candidate = date(today.year, today.month, min(day, last_day_this_month))
    if candidate >= today:
        return candidate

    year, month = today.year, today.month + 1
    if month > 12:
        month = 1
        year += 1
    last_day_next_month = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last_day_next_month))


def get_next_statement_date(details, today=None):
    """Próxima fecha de corte (próxima ocurrencia de `statement_day`)."""
    return _next_occurrence_of_day(details.statement_day, today)


def get_next_payment_due_date(details, today=None):
    """Próxima fecha límite de pago (próxima ocurrencia de `payment_due_day`)."""
    return _next_occurrence_of_day(details.payment_due_day, today)
