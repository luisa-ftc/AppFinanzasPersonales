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


def get_balances_by_currency(user):
    """Agrupa los saldos calculados de todas las cuentas activas por moneda.

    Devuelve un dict ordenado {currency: Decimal}. Las transferencias entre
    cuentas de la misma moneda se cancelan correctamente; entre distintas
    monedas aparecen como movimientos independientes en cada entrada.
    """
    result: dict = {}
    for account in user.accounts.filter(is_active=True):
        currency = account.currency or "?"
        balance = calculate_account_balance(account)
        result[currency] = result.get(currency, Decimal("0")) + balance
    return dict(sorted(result.items()))


def get_user_total_balance(user):
    """Suma los saldos calculados de todas las cuentas activas del usuario."""
    total = Decimal("0")
    for account in user.accounts.filter(is_active=True):
        total += calculate_account_balance(account)
    return total
