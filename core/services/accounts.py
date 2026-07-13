"""Cálculo de saldos de cuentas y usuario, derivados de las transacciones (no persistidos)."""

from decimal import Decimal

from django.db.models import Sum


def calculate_account_balance(account):
    """Calcula el saldo actual: saldo inicial + ingresos - gastos - transferencias
    salientes + transferencias entrantes."""
    balance = account.initial_balance

    income = account.transactions.filter(
        transaction_type="income"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    expense = account.transactions.filter(
        transaction_type="expense"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    transfers_out = account.transactions.filter(
        transaction_type="transfer"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    transfers_in = account.incoming_transfers.filter(
        transaction_type="transfer"
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    return balance + income - expense - transfers_out + transfers_in


def get_user_total_balance(user):
    """Suma los saldos calculados de todas las cuentas activas del usuario."""
    total = Decimal("0")
    for account in user.accounts.filter(is_active=True):
        total += calculate_account_balance(account)
    return total
