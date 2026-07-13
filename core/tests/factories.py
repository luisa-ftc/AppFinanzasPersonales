"""Factories de factory_boy para generar datos de prueba de FinTrack.

`AccountFactory`, `CategoryFactory` y `TransactionFactory` encadenan el mismo
usuario entre sí mediante `SelfAttribute`, para que una transacción de
prueba nunca quede asociada a una cuenta o categoría de otro usuario.
"""

import factory
from decimal import Decimal

from core.models import Account, AccountCreditCardDetails, Category, Debt, Transaction, User


class UserFactory(factory.django.DjangoModelFactory):
    """Genera usuarios de prueba con email/username secuenciales y contraseña fija."""

    class Meta:
        model = User

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    username = factory.Sequence(lambda n: f"user{n}")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class AccountFactory(factory.django.DjangoModelFactory):
    """Genera cuentas de prueba con saldo inicial fijo, cada una con su propio usuario."""

    class Meta:
        model = Account

    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Cuenta {n}")
    initial_balance = Decimal("1000.00")


class AccountCreditCardDetailsFactory(factory.django.DjangoModelFactory):
    """Genera detalles de tarjeta de crédito de prueba, ligados a una cuenta de tipo crédito."""

    class Meta:
        model = AccountCreditCardDetails

    account = factory.SubFactory(AccountFactory, account_type="credit")
    credit_limit = Decimal("5000.00")
    statement_day = 5
    payment_due_day = 20


class CategoryFactory(factory.django.DjangoModelFactory):
    """Genera categorías de gasto de prueba, cada una con su propio usuario."""

    class Meta:
        model = Category

    user = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Categoría {n}")
    category_type = "expense"


class DebtFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Debt

    user = factory.SubFactory(UserFactory)
    nombre = factory.Sequence(lambda n: f"Deuda {n}")
    prestamista = "Prestamista Test"
    monto_requerido = Decimal("1000.00")
    monto_pagado = Decimal("0.00")
    fecha_limite = "2026-12-31"


class TransactionFactory(factory.django.DjangoModelFactory):
    """Genera transacciones de gasto de prueba, reutilizando el mismo usuario en
    cuenta y categoría (vía `SelfAttribute`) para mantener consistencia de dueño."""

    class Meta:
        model = Transaction

    user = factory.SubFactory(UserFactory)
    account = factory.SubFactory(AccountFactory, user=factory.SelfAttribute("..user"))
    category = factory.SubFactory(CategoryFactory, user=factory.SelfAttribute("..user"))
    transaction_type = "expense"
    amount = Decimal("100.00")
    description = factory.Sequence(lambda n: f"Transacción {n}")
    date = "2026-01-15"
