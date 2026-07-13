"""Comando de gestión `setup_demo`: carga datos de demostración para probar FinTrack."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import (
    Account,
    AccountCreditCardDetails,
    Budget,
    Category,
    Tag,
    Transaction,
    User,
)


class Command(BaseCommand):
    """Crea (si no existen) un usuario demo, cuentas, categorías, una etiqueta,
    un presupuesto y transacciones de ejemplo.

    Es idempotente: usa `get_or_create` y hash de contenido para poder
    ejecutarse varias veces sin duplicar datos.
    """

    help = "Carga datos de demostración con usuario demo@fintrack.local / demo1234"

    def handle(self, *args, **options):
        """Ejecuta la carga de datos demo descrita en la docstring de la clase."""
        user, created = User.objects.get_or_create(
            email="demo@fintrack.local",
            defaults={
                "username": "demo",
                "first_name": "Usuario",
                "last_name": "Demo",
            },
        )
        if created:
            user.set_password("demo1234")
            user.save()
            self.stdout.write("Usuario demo creado (demo@fintrack.local / demo1234)")
        else:
            self.stdout.write("Usuario demo ya existe")

        checking, _ = Account.objects.get_or_create(
            user=user,
            name="Cuenta Principal",
            defaults={
                "account_type": "checking",
                "initial_balance": Decimal("5000.00"),
            },
        )
        savings, _ = Account.objects.get_or_create(
            user=user,
            name="Ahorros",
            defaults={
                "account_type": "savings",
                "initial_balance": Decimal("10000.00"),
            },
        )

        salary_cat, _ = Category.objects.get_or_create(
            user=user,
            name="Salario",
            category_type="income",
            defaults={"color": "#22c55e"},
        )
        food_cat, _ = Category.objects.get_or_create(
            user=user,
            name="Comida",
            category_type="expense",
            defaults={"color": "#ef4444"},
        )
        transport_cat, _ = Category.objects.get_or_create(
            user=user,
            name="Transporte",
            category_type="expense",
            defaults={"color": "#f59e0b"},
        )

        credit_card, _ = Account.objects.get_or_create(
            user=user,
            name="Tarjeta Visa",
            defaults={
                "account_type": "credit",
                "initial_balance": Decimal("1500.00"),
            },
        )
        AccountCreditCardDetails.objects.get_or_create(
            account=credit_card,
            defaults={
                "credit_limit": Decimal("8000.00"),
                "statement_day": 5,
                "payment_due_day": 20,
            },
        )

        Tag.objects.get_or_create(user=user, name="recurrente", defaults={"color": "#6366f1"})

        Budget.objects.get_or_create(
            user=user,
            category=food_cat,
            period_start="2026-01-01",
            period_end="2026-01-31",
            defaults={"amount": Decimal("2000.00"), "notes": "Presupuesto mensual de comida"},
        )

        sample_transactions = [
            ("2026-01-01", "income", salary_cat, checking, Decimal("15000.00"), "Salario enero"),
            ("2026-01-05", "expense", food_cat, checking, Decimal("850.00"), "Supermercado"),
            ("2026-01-10", "expense", transport_cat, checking, Decimal("350.00"), "Gasolina"),
            ("2026-01-15", "expense", food_cat, checking, Decimal("120.00"), "Restaurante"),
            ("2026-02-01", "income", salary_cat, checking, Decimal("15000.00"), "Salario febrero"),
            ("2026-02-08", "expense", food_cat, checking, Decimal("920.00"), "Supermercado"),
            ("2026-02-12", "transfer", None, checking, Decimal("2000.00"), "Ahorro mensual"),
        ]

        for date, tx_type, category, account, amount, desc in sample_transactions:
            tx_hash = Transaction.compute_hash(user.pk, account.pk, date, amount, desc)
            if not Transaction.objects.filter(user=user, content_hash=tx_hash).exists():
                tx = Transaction(
                    user=user,
                    account=account,
                    category=category,
                    transaction_type=tx_type,
                    amount=amount,
                    description=desc,
                    date=date,
                )
                if tx_type == "transfer":
                    tx.transfer_to_account = savings
                tx.save()

        self.stdout.write(self.style.SUCCESS("Datos de demostración cargados correctamente."))
