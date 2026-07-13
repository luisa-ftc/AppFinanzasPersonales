"""Comando de gestión `setup_demo`: carga datos de demostración para probar FinTrack."""

from decimal import Decimal

from django.core.management.base import BaseCommand

from core.models import (
    Account,
    AccountCreditCardDetails,
    Budget,
    Category,
    Contact,
    ContactGroup,
    Debt,
    Goal,
    SharedExpense,
    Tag,
    Transaction,
    User,
)
from core.services.contacts import add_contact
from core.services.shared_expenses import (
    ParticipantSpec,
    build_participant_specs,
    create_shared_expense,
    register_shared_expense_payment,
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
                "currency": "COP",
                "initial_balance": Decimal("5000.00"),
            },
        )
        savings, _ = Account.objects.get_or_create(
            user=user,
            name="Ahorros",
            defaults={
                "account_type": "savings",
                "currency": "COP",
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
                "currency": "COP",
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

        Debt.objects.get_or_create(
            user=user,
            nombre="Préstamo personal",
            defaults={
                "prestamista": "Banco Nacional",
                "monto_requerido": Decimal("5000000.00"),
                "monto_pagado": Decimal("1500000.00"),
                "fecha_limite": "2026-12-31",
                "observaciones": "Préstamo a 12 meses",
            },
        )
        Debt.objects.get_or_create(
            user=user,
            nombre="Deuda familiar",
            defaults={
                "prestamista": "Mamá",
                "monto_requerido": Decimal("800000.00"),
                "monto_pagado": Decimal("800000.00"),
                "fecha_limite": "2026-03-01",
                "observaciones": "Ya pagada",
            },
        )

        Goal.objects.get_or_create(
            user=user,
            nombre="Viaje a Japón",
            defaults={
                "monto_requerido": Decimal("5000000.00"),
                "monto_abonado": Decimal("1500000.00"),
                "fecha_limite": "2026-12-31",
                "observaciones": "Meta de ahorro para vacaciones",
            },
        )
        Goal.objects.get_or_create(
            user=user,
            nombre="Fondo de emergencia",
            defaults={
                "monto_requerido": Decimal("2000000.00"),
                "monto_abonado": Decimal("2000000.00"),
                "fecha_limite": "2026-06-01",
                "observaciones": "Meta completada",
            },
        )

        # Usuarios extra para probar el módulo de Contactos (búsqueda y relación
        # bidireccional). Idempotentes, misma contraseña que el usuario demo.
        juan, juan_created = User.objects.get_or_create(
            email="juan@fintrack.local",
            defaults={
                "username": "juan",
                "first_name": "Juan",
                "last_name": "Pérez",
            },
        )
        if juan_created:
            juan.set_password("demo1234")
            juan.save()
        maria, maria_created = User.objects.get_or_create(
            email="maria@fintrack.local",
            defaults={
                "username": "maria",
                "first_name": "María",
                "last_name": "Gómez",
            },
        )
        if maria_created:
            maria.set_password("demo1234")
            maria.save()
        add_contact(user, juan)
        add_contact(user, maria)

        # Grupo demo con el contacto Juan como integrante (add es idempotente).
        grupo, _ = ContactGroup.objects.get_or_create(
            user=user,
            name="Viaje Cartagena",
            defaults={"description": "Gastos del viaje a Cartagena"},
        )
        contacto_juan = Contact.objects.get(user=user, contact=juan)
        contacto_maria = Contact.objects.get(user=user, contact=maria)
        grupo.members.add(contacto_juan)

        # Gasto compartido demo: el dueño paga, Juan y María son participantes,
        # con un pago parcial de Juan ya registrado.
        if not SharedExpense.objects.filter(user=user, name="Cena grupo Cartagena").exists():
            specs = build_participant_specs(True, [contacto_juan, contacto_maria])
            shared_expense = create_shared_expense(
                user=user,
                name="Cena grupo Cartagena",
                description="Cena del viaje a Cartagena",
                account=checking,
                category=food_cat,
                date="2026-02-20",
                total_amount=Decimal("150000.00"),
                participant_specs=specs,
                payer_spec=ParticipantSpec(True, None),
            )
            juan_participant = shared_expense.participants.get(contact=contacto_juan)
            register_shared_expense_payment(
                participant=juan_participant,
                amount=Decimal("20000.00"),
                date="2026-02-25",
                notes="Abono parcial",
                account=checking,
            )

        # Segundo gasto demo: pagó un contacto (Juan), no el dueño. No genera
        # ninguna Transacción de gasto — es puramente informativo.
        if not SharedExpense.objects.filter(user=user, name="Peaje pagado por Juan").exists():
            specs_juan_paga = build_participant_specs(True, [contacto_juan])
            juan_spec = next(s for s in specs_juan_paga if s.contact == contacto_juan)
            peaje = create_shared_expense(
                user=user,
                name="Peaje pagado por Juan",
                description="Juan adelantó el peaje del viaje",
                account=None,
                category=transport_cat,
                date="2026-02-18",
                total_amount=Decimal("30000.00"),
                participant_specs=specs_juan_paga,
                payer_spec=juan_spec,
            )
            # El dueño salda su propia parte: sí genera una Transacción de gasto real.
            owner_participant_peaje = peaje.participants.get(is_owner=True)
            register_shared_expense_payment(
                participant=owner_participant_peaje,
                amount=Decimal("15000.00"),
                date="2026-02-22",
                notes="Le pagué a Juan mi parte del peaje",
                account=checking,
            )

        self.stdout.write(self.style.SUCCESS("Datos de demostración cargados correctamente."))
