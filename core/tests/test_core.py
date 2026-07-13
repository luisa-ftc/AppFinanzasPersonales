"""Suite de tests de FinTrack (pytest + pytest-django).

Cubre: configuración de seguridad, el modelo de usuario, cálculo de saldo de
cuentas, reportes por mes, detección de duplicados por hash, importación
CSV, la API de cuentas/transacciones y las vistas web de autenticación.
"""

import pytest
from copy import copy
from decimal import Decimal
from datetime import date
from types import SimpleNamespace

from django.conf import settings
from django.template import Context
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.admin import UserAdmin
from core.models import (
    Account,
    AccountCreditCardDetails,
    Budget,
    Category,
    Contact,
    ContactGroup,
    Debt,
    Goal,
    Transaction,
)
from core.services.accounts import calculate_account_balance
from core.services.budgets import calculate_budget_spent
from core.services.contacts import add_contact, remove_contact, search_users
from core.services.credit_cards import (
    get_available_credit,
    get_next_payment_due_date,
    get_next_statement_date,
    get_used_credit,
)
from core.services.csv_io import import_transactions_csv
from core.services.debts import apply_transaction_to_debt, revert_transaction_from_debt, validate_expense_against_debt
from core.services.goals import (
    apply_transaction_to_goal,
    revert_transaction_from_goal,
    validate_expense_against_goal,
    validate_income_against_goal,
)
from core.services.reports import get_monthly_income_expense

User = get_user_model()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="test@example.com",
        username="testuser",
        password="testpass123",
    )


@pytest.fixture
def api_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def account(user):
    return Account.objects.create(
        user=user,
        name="Cuenta Principal",
        initial_balance=Decimal("1000.00"),
    )


@pytest.fixture
def category(user):
    return Category.objects.create(
        user=user,
        name="Comida",
        category_type="expense",
    )


class TestSecuritySettings:
    """Verifica configuración de seguridad y compatibilidad del runtime, no lógica de negocio."""

    def test_csrf_trusted_origins_include_local_development_hosts(self):
        assert "http://localhost:8000" in settings.CSRF_TRUSTED_ORIGINS
        assert "http://127.0.0.1:8000" in settings.CSRF_TRUSTED_ORIGINS

    def test_context_copy_works_with_current_python_runtime(self):
        """Confirma que `copy()` sobre un `Context` de Django sigue funcionando en esta versión de Python."""
        context = Context({"value": 1})
        copied = copy(context)
        assert copied.get("value") == 1


@pytest.mark.django_db
class TestUserModel:
    """Verifica la unicidad del email y la configuración del admin de usuarios."""

    def test_email_unique(self, user):
        with pytest.raises(Exception):
            User.objects.create_user(
                email="test@example.com",
                username="other",
                password="pass",
            )

    def test_user_admin_fieldsets_do_not_repeat_email(self):
        """Confirma que el reordenamiento de `fieldsets` en `UserAdmin` no deja el email duplicado."""
        field_names = []
        for _, options in UserAdmin.fieldsets:
            fields = options.get("fields", ())
            if isinstance(fields, str):
                fields = (fields,)
            field_names.extend(fields)

        assert field_names.count("email") == 1


@pytest.mark.django_db
class TestAccountBalance:
    """Verifica que `calculate_account_balance` combine correctamente saldo inicial, ingresos y gastos."""

    def test_balance_with_transactions(self, user, account, category):
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            transaction_type="expense",
            amount=Decimal("100.00"),
            description="Supermercado",
            date="2026-01-15",
        )
        Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="income",
            amount=Decimal("500.00"),
            description="Salario",
            date="2026-01-01",
        )
        balance = calculate_account_balance(account)
        assert balance == Decimal("1400.00")


@pytest.mark.django_db
class TestBudgetSpent:
    """Verifica que `calculate_budget_spent` acumule solo transacciones del mismo
    tipo que la categoría del presupuesto (gasto→gasto, ingreso→ingreso)."""

    def test_expense_budget_only_counts_expenses(self, user, account, category):
        income_category = Category.objects.create(
            user=user,
            name="Salario",
            category_type="income",
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            transaction_type="expense",
            amount=Decimal("100.00"),
            description="Supermercado",
            date="2026-01-10",
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=income_category,
            transaction_type="income",
            amount=Decimal("500.00"),
            description="Salario enero",
            date="2026-01-01",
        )
        budget = Budget.objects.create(
            user=user,
            category=category,
            amount=Decimal("200.00"),
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        assert calculate_budget_spent(budget) == Decimal("100.00")

    def test_income_budget_only_counts_income(self, user, account):
        income_category = Category.objects.create(
            user=user,
            name="Salario",
            category_type="income",
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=income_category,
            transaction_type="income",
            amount=Decimal("15000.00"),
            description="Salario enero",
            date="2026-01-01",
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=income_category,
            transaction_type="expense",
            amount=Decimal("50.00"),
            description="Ajuste erróneo",
            date="2026-01-05",
        )
        budget = Budget.objects.create(
            user=user,
            category=income_category,
            amount=Decimal("10000.00"),
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        assert calculate_budget_spent(budget) == Decimal("15000.00")

    def test_transactions_outside_period_are_excluded(self, user, account, category):
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            transaction_type="expense",
            amount=Decimal("999.00"),
            description="Fuera de periodo",
            date="2026-02-01",
        )
        budget = Budget.objects.create(
            user=user,
            category=category,
            amount=Decimal("200.00"),
            period_start="2026-01-01",
            period_end="2026-01-31",
        )
        assert calculate_budget_spent(budget) == Decimal("0")


@pytest.mark.django_db
class TestReports:
    """Verifica que `get_monthly_income_expense` agregue correctamente los montos del mes actual."""

    def test_monthly_income_expense_handles_date_values(self, user, account, category):
        today = date.today().replace(day=15)
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            transaction_type="expense",
            amount=Decimal("100.00"),
            description="Supermercado",
            date=today,
        )
        Transaction.objects.create(
            user=user,
            account=account,
            category=category,
            transaction_type="income",
            amount=Decimal("200.00"),
            description="Salario",
            date=today,
        )

        report = get_monthly_income_expense(user, months=2)

        assert report["income"][-1] == 200.0
        assert report["expense"][-1] == 100.0


@pytest.mark.django_db
class TestTransactionHash:
    """Verifica que `Transaction.compute_hash` sea determinístico y coincida con el `content_hash` guardado."""

    def test_duplicate_hash_detection(self, user, account):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("50.00"),
            description="Cafe",
            date="2026-02-01",
        )
        duplicate_hash = Transaction.compute_hash(
            user.pk, account.pk, tx.date, tx.amount, tx.description
        )
        assert tx.content_hash == duplicate_hash


@pytest.mark.django_db
class TestCSVImport:
    """Verifica que `import_transactions_csv` omita filas ya importadas previamente (mismo hash)."""

    def test_import_skips_duplicates(self, user, account):
        csv_content = (
            "date,account,category,transaction_type,amount,description,notes\n"
            "2026-03-01,Cuenta Principal,,expense,50.00,Cafe,\n"
        )
        result1 = import_transactions_csv(user, csv_content)
        assert result1["created"] == 1

        result2 = import_transactions_csv(user, csv_content)
        assert result2["created"] == 0
        assert result2["skipped_duplicates"] == 1


@pytest.mark.django_db
class TestAccountAPI:
    """Verifica el CRUD de cuentas vía API y el aislamiento por usuario en el listado."""

    def test_create_account(self, api_client):
        url = reverse("account-list")
        response = api_client.post(
            url,
            {"name": "Ahorros", "account_type": "savings", "currency": "MXN"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert Account.objects.count() == 1

    def test_list_accounts_scoped_to_user(self, api_client, user, account):
        other = User.objects.create_user(
            email="other@example.com", username="other", password="pass"
        )
        Account.objects.create(user=other, name="Otra cuenta")

        url = reverse("account-list")
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert len(response.data["results"]) == 1


@pytest.mark.django_db
class TestAccountCreditCardAPI:
    """Verifica el CRUD de cuentas de tipo tarjeta de crédito (detalle anidado) vía API."""

    def test_create_credit_account_with_details(self, api_client):
        url = reverse("account-list")
        response = api_client.post(
            url,
            {
                "name": "Tarjeta Visa",
                "account_type": "credit",
                "currency": "COL",
                "credit_card_details": {
                    "credit_limit": "5000.00",
                    "statement_day": 5,
                    "payment_due_day": 20,
                },
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert AccountCreditCardDetails.objects.count() == 1
        details = AccountCreditCardDetails.objects.get()
        assert details.credit_limit == Decimal("5000.00")
        assert details.statement_day == 5

    def test_update_credit_card_details(self, api_client, user):
        account = Account.objects.create(user=user, name="Tarjeta", account_type="credit")
        AccountCreditCardDetails.objects.create(
            account=account,
            credit_limit=Decimal("3000.00"),
            statement_day=1,
            payment_due_day=10,
        )
        url = reverse("account-detail", kwargs={"pk": account.pk})
        response = api_client.patch(
            url,
            {
                "credit_card_details": {
                    "credit_limit": "6000.00",
                    "statement_day": 15,
                    "payment_due_day": 25,
                }
            },
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        account.credit_card_details.refresh_from_db()
        assert account.credit_card_details.credit_limit == Decimal("6000.00")
        assert account.credit_card_details.statement_day == 15

    def test_changing_type_away_from_credit_deletes_details(self, api_client, user):
        account = Account.objects.create(user=user, name="Tarjeta", account_type="credit")
        AccountCreditCardDetails.objects.create(
            account=account,
            credit_limit=Decimal("3000.00"),
            statement_day=1,
            payment_due_day=10,
        )
        url = reverse("account-detail", kwargs={"pk": account.pk})
        response = api_client.patch(url, {"account_type": "savings"}, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert AccountCreditCardDetails.objects.filter(account=account).count() == 0


@pytest.mark.django_db
class TestCreditCardService:
    """Verifica los cálculos derivados de tarjetas de crédito en core/services/credit_cards.py."""

    def test_get_used_credit_grows_with_expenses(self, user, account):
        # El fixture `account` ya trae initial_balance=1000.00 (deuda inicial).
        account.account_type = "credit"
        account.save()
        Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("300.00"),
            description="Compra",
            date="2026-01-05",
        )
        assert get_used_credit(account) == Decimal("1300.00")

    def test_get_used_credit_shrinks_with_incoming_payment(self, user, account):
        account.account_type = "credit"
        account.save()
        payer = Account.objects.create(
            user=user, name="Cuenta Pagadora", initial_balance=Decimal("2000.00")
        )
        Transaction.objects.create(
            user=user,
            account=payer,
            transfer_to_account=account,
            transaction_type="transfer",
            amount=Decimal("400.00"),
            description="Pago tarjeta",
            date="2026-01-10",
        )
        assert get_used_credit(account) == Decimal("600.00")

    def test_get_available_credit_clamped_to_zero_when_over_limit(self, user, account):
        account.account_type = "credit"
        account.save()
        Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("6000.00"),
            description="Compra grande",
            date="2026-01-05",
        )
        details = AccountCreditCardDetails.objects.create(
            account=account,
            credit_limit=Decimal("5000.00"),
            statement_day=5,
            payment_due_day=20,
        )
        assert get_available_credit(account, details) == Decimal("0.00")

    def test_next_statement_date_future_this_month(self):
        details = SimpleNamespace(statement_day=20)
        assert get_next_statement_date(details, today=date(2026, 1, 10)) == date(2026, 1, 20)

    def test_next_statement_date_already_passed_goes_to_next_month(self):
        details = SimpleNamespace(statement_day=5)
        assert get_next_statement_date(details, today=date(2026, 1, 10)) == date(2026, 2, 5)

    def test_next_statement_date_today_counts_as_next(self):
        details = SimpleNamespace(statement_day=10)
        assert get_next_statement_date(details, today=date(2026, 1, 10)) == date(2026, 1, 10)

    def test_next_statement_date_clamped_on_short_month(self):
        details = SimpleNamespace(statement_day=31)
        assert get_next_statement_date(details, today=date(2026, 2, 1)) == date(2026, 2, 28)

    def test_next_payment_due_date_uses_payment_due_day(self):
        details = SimpleNamespace(payment_due_day=15)
        assert get_next_payment_due_date(details, today=date(2026, 1, 1)) == date(2026, 1, 15)


@pytest.mark.django_db
class TestAccountCreditCardWebView:
    """Verifica que el formulario web cree/actualice ambos objetos (Account y
    AccountCreditCardDetails) de forma atómica según el tipo elegido."""

    def test_create_credit_account_creates_details(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:account_create"),
            {
                "account_type": "credit",
                "name": "Tarjeta Web",
                "currency": "COL",
                "initial_balance": "0.00",
                "is_active": "on",
                "credit_limit": "4000.00",
                "statement_day": "10",
                "payment_due_day": "25",
            },
        )
        assert response.status_code == 302
        account = Account.objects.get(name="Tarjeta Web")
        assert AccountCreditCardDetails.objects.filter(account=account).exists()

    def test_create_non_credit_account_skips_details(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:account_create"),
            {
                "account_type": "savings",
                "name": "Ahorros Web",
                "currency": "COL",
                "initial_balance": "0.00",
                "is_active": "on",
            },
        )
        assert response.status_code == 302
        account = Account.objects.get(name="Ahorros Web")
        assert not AccountCreditCardDetails.objects.filter(account=account).exists()


@pytest.mark.django_db
class TestTransactionAPI:
    """Verifica la acción `reconcile` del `TransactionViewSet` vía API."""

    def test_reconcile_transaction(self, api_client, user, account):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("25.00"),
            description="Test",
            date="2026-01-10",
        )
        url = reverse("transaction-reconcile", kwargs={"pk": tx.pk})
        response = api_client.post(url)
        assert response.status_code == status.HTTP_200_OK
        tx.refresh_from_db()
        assert tx.is_reconciled is True


@pytest.mark.django_db
class TestAuthViews:
    """Verifica las vistas web de registro y el comportamiento de login por email."""

    def test_register_page(self, client):
        response = client.get(reverse("core:register"))
        assert response.status_code == 200

    def test_login_redirects_authenticated(self, client, user):
        client.login(username="test@example.com", password="testpass123")
        response = client.get(reverse("core:dashboard"))
        assert response.status_code == 200


# ── Debt tests ──────────────────────────────────────────────


@pytest.fixture
def debt(user):
    return Debt.objects.create(
        user=user,
        nombre="Test Debt",
        prestamista="Test Lender",
        monto_requerido=Decimal("1000.00"),
        fecha_limite=date(2026, 12, 31),
    )


@pytest.mark.django_db
class TestDebtModel:
    def test_monto_pendiente(self, debt):
        assert debt.monto_pendiente == Decimal("1000.00")

    def test_estado_pendiente(self, debt):
        assert debt.estado == "pendiente"

    def test_estado_pagada(self, user):
        d = Debt.objects.create(
            user=user,
            nombre="Paid",
            prestamista="X",
            monto_requerido=Decimal("500.00"),
            monto_pagado=Decimal("500.00"),
            fecha_limite=date(2026, 12, 31),
        )
        assert d.estado == "pagada"

    def test_estado_vencida(self, user):
        d = Debt.objects.create(
            user=user,
            nombre="Overdue",
            prestamista="X",
            monto_requerido=Decimal("500.00"),
            fecha_limite=date(2020, 1, 1),
        )
        assert d.estado == "vencida"

    def test_percent_paid(self, user):
        d = Debt.objects.create(
            user=user,
            nombre="Half",
            prestamista="X",
            monto_requerido=Decimal("200.00"),
            monto_pagado=Decimal("100.00"),
            fecha_limite=date(2026, 12, 31),
        )
        assert d.percent_paid == Decimal("50")


@pytest.mark.django_db
class TestDebtTransactionIntegration:
    def test_expense_increases_monto_pagado(self, user, account, debt):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("200.00"),
            description="Abono",
            date="2026-06-01",
            debt=debt,
        )
        apply_transaction_to_debt(tx)
        debt.refresh_from_db()
        assert debt.monto_pagado == Decimal("200.00")

    def test_income_increases_monto_requerido(self, user, account, debt):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="income",
            amount=Decimal("300.00"),
            description="Préstamo adicional",
            date="2026-06-01",
            debt=debt,
        )
        apply_transaction_to_debt(tx)
        debt.refresh_from_db()
        assert debt.monto_requerido == Decimal("1300.00")

    def test_expense_cannot_exceed_pendiente(self, debt):
        with pytest.raises(Exception):
            validate_expense_against_debt(debt, Decimal("1500.00"))

    def test_revert_expense(self, user, account, debt):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("200.00"),
            description="Abono",
            date="2026-06-01",
            debt=debt,
        )
        apply_transaction_to_debt(tx)
        revert_transaction_from_debt(tx)
        debt.refresh_from_db()
        assert debt.monto_pagado == Decimal("0.00")

    def test_no_op_without_debt(self, user, account):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("100.00"),
            description="Normal",
            date="2026-06-01",
        )
        apply_transaction_to_debt(tx)


@pytest.mark.django_db
class TestDebtAPI:
    def test_create_debt(self, api_client):
        response = api_client.post(
            reverse("debt-list"),
            {
                "nombre": "API Debt",
                "prestamista": "Bank",
                "monto_requerido": "500.00",
                "fecha_limite": "2026-12-31",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["monto_pendiente"] == "500.00"

    def test_list_debts_scoped(self, api_client, user):
        Debt.objects.create(
            user=user,
            nombre="Mine",
            prestamista="X",
            monto_requerido=Decimal("100.00"),
            fecha_limite=date(2026, 12, 31),
        )
        other = User.objects.create_user(
            email="other@example.com", username="other", password="pass"
        )
        Debt.objects.create(
            user=other,
            nombre="Theirs",
            prestamista="Y",
            monto_requerido=Decimal("200.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = api_client.get(reverse("debt-list"))
        results = response.data.get("results", response.data)
        assert len(results) == 1
        assert results[0]["nombre"] == "Mine"

    def test_computed_fields(self, api_client, user):
        Debt.objects.create(
            user=user,
            nombre="Comp",
            prestamista="X",
            monto_requerido=Decimal("1000.00"),
            monto_pagado=Decimal("400.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = api_client.get(reverse("debt-list"))
        results = response.data.get("results", response.data)
        d = results[0]
        assert d["monto_pendiente"] == "600.00"
        assert d["estado"] == "pendiente"
        assert Decimal(d["percent_paid"]) == Decimal("40")


@pytest.mark.django_db
class TestDebtWebViews:
    def test_debt_list(self, client, user):
        client.force_login(user)
        response = client.get(reverse("core:debt_list"))
        assert response.status_code == 200

    def test_create_debt_web(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:debt_create"),
            {
                "nombre": "Web Debt",
                "prestamista": "Web Lender",
                "monto_requerido": "1000.00",
                "monto_pagado": "0.00",
                "fecha_limite": "2026-12-31",
            },
        )
        assert response.status_code == 302
        assert Debt.objects.filter(user=user, nombre="Web Debt").exists()

    def test_debt_detail(self, client, user, debt):
        client.force_login(user)
        response = client.get(reverse("core:debt_detail", kwargs={"pk": debt.pk}))
        assert response.status_code == 200


# ── Goal (Metas) tests ──────────────────────────────────────


@pytest.fixture
def goal(user):
    return Goal.objects.create(
        user=user,
        nombre="Viaje a Japón",
        monto_requerido=Decimal("5000000.00"),
        fecha_limite=date(2026, 12, 31),
    )


@pytest.mark.django_db
class TestGoalModel:
    def test_monto_pendiente(self, goal):
        assert goal.monto_pendiente == Decimal("5000000.00")

    def test_estado_pendiente(self, goal):
        assert goal.estado == "pendiente"

    def test_estado_completada(self, user):
        g = Goal.objects.create(
            user=user,
            nombre="Completa",
            monto_requerido=Decimal("500.00"),
            monto_abonado=Decimal("500.00"),
            fecha_limite=date(2026, 12, 31),
        )
        assert g.estado == "completada"

    def test_percent_abonado(self, user):
        g = Goal.objects.create(
            user=user,
            nombre="Media",
            monto_requerido=Decimal("200.00"),
            monto_abonado=Decimal("100.00"),
            fecha_limite=date(2026, 12, 31),
        )
        assert g.percent_abonado == Decimal("50")


@pytest.mark.django_db
class TestGoalTransactionIntegration:
    def test_income_increases_monto_abonado(self, user, account, goal):
        goal.monto_abonado = Decimal("1000000.00")
        goal.save()
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="income",
            amount=Decimal("500000.00"),
            description="Aporte",
            date="2026-06-01",
            goal=goal,
        )
        apply_transaction_to_goal(tx)
        goal.refresh_from_db()
        assert goal.monto_abonado == Decimal("1500000.00")

    def test_expense_decreases_monto_abonado(self, user, account, goal):
        goal.monto_abonado = Decimal("2000000.00")
        goal.save()
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="expense",
            amount=Decimal("300000.00"),
            description="Retiro",
            date="2026-06-01",
            goal=goal,
        )
        apply_transaction_to_goal(tx)
        goal.refresh_from_db()
        assert goal.monto_abonado == Decimal("1700000.00")

    def test_income_cannot_exceed_requerido(self, user):
        g = Goal.objects.create(
            user=user,
            nombre="Tope",
            monto_requerido=Decimal("1000.00"),
            monto_abonado=Decimal("900.00"),
            fecha_limite=date(2026, 12, 31),
        )
        with pytest.raises(Exception):
            validate_income_against_goal(g, Decimal("200.00"))

    def test_expense_cannot_go_negative(self, user):
        g = Goal.objects.create(
            user=user,
            nombre="Fondo",
            monto_requerido=Decimal("1000.00"),
            monto_abonado=Decimal("100.00"),
            fecha_limite=date(2026, 12, 31),
        )
        with pytest.raises(Exception):
            validate_expense_against_goal(g, Decimal("200.00"))

    def test_revert_income(self, user, account, goal):
        goal.monto_abonado = Decimal("1000000.00")
        goal.save()
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="income",
            amount=Decimal("500000.00"),
            description="Aporte",
            date="2026-06-01",
            goal=goal,
        )
        apply_transaction_to_goal(tx)
        revert_transaction_from_goal(tx)
        goal.refresh_from_db()
        assert goal.monto_abonado == Decimal("1000000.00")

    def test_no_op_without_goal(self, user, account):
        tx = Transaction.objects.create(
            user=user,
            account=account,
            transaction_type="income",
            amount=Decimal("100.00"),
            description="Normal",
            date="2026-06-01",
        )
        apply_transaction_to_goal(tx)  # no debe lanzar


@pytest.mark.django_db
class TestGoalAPI:
    def test_create_goal(self, api_client):
        response = api_client.post(
            reverse("goal-list"),
            {
                "nombre": "API Meta",
                "monto_requerido": "500.00",
                "fecha_limite": "2026-12-31",
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["monto_pendiente"] == "500.00"

    def test_list_goals_scoped(self, api_client, user):
        Goal.objects.create(
            user=user,
            nombre="Mía",
            monto_requerido=Decimal("100.00"),
            fecha_limite=date(2026, 12, 31),
        )
        other = User.objects.create_user(
            email="other2@example.com", username="other2", password="pass"
        )
        Goal.objects.create(
            user=other,
            nombre="Ajena",
            monto_requerido=Decimal("200.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = api_client.get(reverse("goal-list"))
        results = response.data.get("results", response.data)
        assert len(results) == 1
        assert results[0]["nombre"] == "Mía"

    def test_computed_fields(self, api_client, user):
        Goal.objects.create(
            user=user,
            nombre="Comp",
            monto_requerido=Decimal("1000.00"),
            monto_abonado=Decimal("400.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = api_client.get(reverse("goal-list"))
        results = response.data.get("results", response.data)
        g = results[0]
        assert g["monto_pendiente"] == "600.00"
        assert g["estado"] == "pendiente"
        assert Decimal(g["percent_abonado"]) == Decimal("40")

    def test_income_updates_goal(self, api_client, user, account, goal):
        response = api_client.post(
            reverse("transaction-list"),
            {
                "account": account.pk,
                "transaction_type": "income",
                "amount": "1000000.00",
                "description": "Aporte API",
                "date": "2026-06-01",
                "goal": goal.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        goal.refresh_from_db()
        assert goal.monto_abonado == Decimal("1000000.00")

    def test_cannot_link_debt_and_goal(self, api_client, user, account, goal, debt):
        response = api_client.post(
            reverse("transaction-list"),
            {
                "account": account.pk,
                "transaction_type": "income",
                "amount": "100.00",
                "description": "Ambos",
                "date": "2026-06-01",
                "debt": debt.pk,
                "goal": goal.pk,
            },
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestGoalWebViews:
    def test_goal_list(self, client, user):
        client.force_login(user)
        response = client.get(reverse("core:goal_list"))
        assert response.status_code == 200

    def test_create_goal_web(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:goal_create"),
            {
                "nombre": "Web Meta",
                "monto_requerido": "1000.00",
                "monto_abonado": "0.00",
                "fecha_limite": "2026-12-31",
            },
        )
        assert response.status_code == 302
        assert Goal.objects.filter(user=user, nombre="Web Meta").exists()

    def test_goal_detail(self, client, user, goal):
        client.force_login(user)
        response = client.get(reverse("core:goal_detail", kwargs={"pk": goal.pk}))
        assert response.status_code == 200

    def test_goal_update_web(self, client, user, goal):
        client.force_login(user)
        get_response = client.get(reverse("core:goal_update", kwargs={"pk": goal.pk}))
        assert get_response.status_code == 200
        response = client.post(
            reverse("core:goal_update", kwargs={"pk": goal.pk}),
            {
                "nombre": "Viaje a Japón",
                "monto_requerido": "6000000.00",
                "monto_abonado": "1500000.00",
                "fecha_limite": "2027-01-31",
            },
        )
        assert response.status_code == 302
        goal.refresh_from_db()
        assert goal.monto_requerido == Decimal("6000000.00")
        assert goal.monto_abonado == Decimal("1500000.00")

    def test_goal_delete_web(self, client, user, goal):
        client.force_login(user)
        response = client.post(reverse("core:goal_delete", kwargs={"pk": goal.pk}))
        assert response.status_code == 302
        assert not Goal.objects.filter(pk=goal.pk).exists()

    def test_goal_list_shows_progress_and_pending(self, client, user):
        client.force_login(user)
        Goal.objects.create(
            user=user,
            nombre="Progreso",
            monto_requerido=Decimal("1000.00"),
            monto_abonado=Decimal("250.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = client.get(reverse("core:goal_list"))
        content = response.content.decode()
        assert "25%" in content  # porcentaje de progreso
        assert "750.00" in content  # monto pendiente calculado


@pytest.mark.django_db
class TestAsociarAField:
    def test_associate_income_to_goal(self, client, user, account, goal):
        client.force_login(user)
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "income",
                "amount": "1000000.00",
                "description": "Aporte",
                "date": "2026-06-01",
                "asociar_a": f"goal:{goal.pk}",
            },
        )
        assert response.status_code == 302
        goal.refresh_from_db()
        assert goal.monto_abonado == Decimal("1000000.00")
        tx = Transaction.objects.get(user=user, description="Aporte")
        assert tx.goal_id == goal.pk
        assert tx.debt_id is None

    def test_associate_expense_to_debt_still_works(self, client, user, account, debt):
        client.force_login(user)
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "expense",
                "amount": "200.00",
                "description": "Abono deuda",
                "date": "2026-06-01",
                "asociar_a": f"debt:{debt.pk}",
            },
        )
        assert response.status_code == 302
        debt.refresh_from_db()
        assert debt.monto_pagado == Decimal("200.00")

    def test_income_overpay_goal_blocked(self, client, user, account):
        client.force_login(user)
        g = Goal.objects.create(
            user=user,
            nombre="Tope",
            monto_requerido=Decimal("1000.00"),
            monto_abonado=Decimal("900.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "income",
                "amount": "500.00",
                "description": "Sobre-abono",
                "date": "2026-06-01",
                "asociar_a": f"goal:{g.pk}",
            },
        )
        assert response.status_code == 200  # re-render con error
        assert not Transaction.objects.filter(description="Sobre-abono").exists()
        g.refresh_from_db()
        assert g.monto_abonado == Decimal("900.00")

    def test_transfer_clears_association(self, client, user, account, goal):
        client.force_login(user)
        other = Account.objects.create(
            user=user, name="Destino", account_type="savings"
        )
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "transfer",
                "amount": "100.00",
                "description": "Transferencia",
                "date": "2026-06-01",
                "transfer_to_account": other.pk,
                "asociar_a": f"goal:{goal.pk}",
            },
        )
        assert response.status_code == 302
        tx = Transaction.objects.get(user=user, description="Transferencia")
        assert tx.goal_id is None
        assert tx.debt_id is None

    def test_form_renders_asociar_a(self, client, user):
        client.force_login(user)
        response = client.get(reverse("core:transaction_create"))
        assert response.status_code == 200
        assert b"Asociar a" in response.content

    def test_expense_to_goal_web(self, client, user, account):
        client.force_login(user)
        g = Goal.objects.create(
            user=user,
            nombre="Fondo",
            monto_requerido=Decimal("5000000.00"),
            monto_abonado=Decimal("2000000.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "expense",
                "amount": "300000.00",
                "description": "Retiro meta",
                "date": "2026-06-01",
                "asociar_a": f"goal:{g.pk}",
            },
        )
        assert response.status_code == 302
        g.refresh_from_db()
        assert g.monto_abonado == Decimal("1700000.00")
        assert g.monto_pendiente == Decimal("3300000.00")

    def test_asociar_a_shows_type_labels(self, client, user, debt, goal):
        client.force_login(user)
        response = client.get(reverse("core:transaction_create"))
        content = response.content.decode()
        assert "(Deuda)" in content
        assert "(Meta)" in content

    def test_expense_withdraw_cannot_exceed_abonado_web(self, client, user, account):
        client.force_login(user)
        g = Goal.objects.create(
            user=user,
            nombre="Escaso",
            monto_requerido=Decimal("5000000.00"),
            monto_abonado=Decimal("100000.00"),
            fecha_limite=date(2026, 12, 31),
        )
        response = client.post(
            reverse("core:transaction_create"),
            {
                "account": account.pk,
                "transaction_type": "expense",
                "amount": "500000.00",
                "description": "Retiro excesivo",
                "date": "2026-06-01",
                "asociar_a": f"goal:{g.pk}",
            },
        )
        assert response.status_code == 200  # re-render con error
        assert not Transaction.objects.filter(description="Retiro excesivo").exists()
        g.refresh_from_db()
        assert g.monto_abonado == Decimal("100000.00")


# ── Contact (Contactos) tests ───────────────────────────────


@pytest.fixture
def juan(db):
    return User.objects.create_user(
        email="juan@example.com",
        username="juan",
        password="pass",
        first_name="Juan",
        last_name="Pérez",
    )


@pytest.fixture
def maria(db):
    return User.objects.create_user(
        email="maria@example.com",
        username="maria",
        password="pass",
        first_name="María",
        last_name="Gómez",
    )


@pytest.mark.django_db
class TestContactModel:
    def test_clean_rejects_self(self, user):
        c = Contact(user=user, contact=user)
        with pytest.raises(Exception):
            c.clean()

    def test_unique_together(self, user, juan):
        Contact.objects.create(user=user, contact=juan)
        with pytest.raises(Exception):
            Contact.objects.create(user=user, contact=juan)

    def test_default_status(self, user, juan):
        c = Contact.objects.create(user=user, contact=juan)
        assert c.status == "contacto"


@pytest.mark.django_db
class TestContactService:
    def test_add_creates_mirror_rows(self, user, juan):
        add_contact(user, juan)
        assert Contact.objects.filter(user=user, contact=juan).exists()
        assert Contact.objects.filter(user=juan, contact=user).exists()

    def test_add_is_idempotent(self, user, juan):
        add_contact(user, juan)
        add_contact(user, juan)
        add_contact(juan, user)  # tampoco duplica desde el otro lado
        assert Contact.objects.count() == 2

    def test_add_self_raises(self, user):
        with pytest.raises(Exception):
            add_contact(user, user)
        assert Contact.objects.count() == 0

    def test_remove_deletes_both_rows(self, user, juan):
        add_contact(user, juan)
        remove_contact(user, juan)
        assert Contact.objects.count() == 0

    def test_search_excludes_self_and_existing(self, user, juan, maria):
        add_contact(user, juan)
        results = list(search_users(user, "example.com"))
        assert maria in results
        assert juan not in results  # ya es contacto
        assert user not in results  # nunca a sí mismo

    def test_search_short_query_returns_empty(self, user, juan):
        assert list(search_users(user, "j")) == []
        assert list(search_users(user, "")) == []


@pytest.mark.django_db
class TestContactWebViews:
    def test_contact_list_scoped(self, client, user, juan, maria):
        add_contact(user, juan)
        add_contact(juan, maria)  # relación ajena, no debe verse
        client.force_login(user)
        response = client.get(reverse("core:contact_list"))
        assert response.status_code == 200
        content = response.content.decode()
        assert "juan@example.com" in content
        assert "maria@example.com" not in content

    def test_add_contact_web_bidirectional(self, client, user, juan):
        client.force_login(user)
        response = client.post(
            reverse("core:contact_create"), {"contact_id": juan.pk}
        )
        assert response.status_code == 302
        assert Contact.objects.filter(user=user, contact=juan).exists()
        assert Contact.objects.filter(user=juan, contact=user).exists()

    def test_add_self_blocked_web(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:contact_create"), {"contact_id": user.pk}
        )
        assert response.status_code == 200  # re-render con error
        assert Contact.objects.count() == 0

    def test_add_nonexistent_blocked_web(self, client, user):
        client.force_login(user)
        response = client.post(
            reverse("core:contact_create"), {"contact_id": 999999}
        )
        assert response.status_code == 200  # re-render con error
        assert Contact.objects.count() == 0

    def test_delete_removes_both_rows(self, client, user, juan):
        add_contact(user, juan)
        row = Contact.objects.get(user=user, contact=juan)
        client.force_login(user)
        response = client.post(
            reverse("core:contact_delete", kwargs={"pk": row.pk})
        )
        assert response.status_code == 302
        assert Contact.objects.count() == 0
        assert User.objects.filter(pk=juan.pk).exists()  # el usuario no se borra

    def test_contact_detail(self, client, user, juan):
        add_contact(user, juan)
        row = Contact.objects.get(user=user, contact=juan)
        client.force_login(user)
        response = client.get(
            reverse("core:contact_detail", kwargs={"pk": row.pk})
        )
        assert response.status_code == 200
        assert b"juan@example.com" in response.content

    def test_search_endpoint_json(self, client, user, juan, maria):
        client.force_login(user)
        response = client.get(reverse("core:contact_search"), {"q": "maria"})
        assert response.status_code == 200
        data = response.json()
        emails = [r["email"] for r in data["results"]]
        assert "maria@example.com" in emails
        assert user.email not in emails


@pytest.mark.django_db
class TestContactAPI:
    def test_create_contact_mirrors(self, api_client, user, juan):
        response = api_client.post(
            reverse("contact-list"), {"contact": juan.pk}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["contact_email"] == "juan@example.com"
        assert Contact.objects.filter(user=juan, contact=user).exists()

    def test_create_self_blocked(self, api_client, user):
        response = api_client.post(
            reverse("contact-list"), {"contact": user.pk}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert Contact.objects.count() == 0

    def test_duplicate_does_not_duplicate(self, api_client, user, juan):
        api_client.post(reverse("contact-list"), {"contact": juan.pk}, format="json")
        response = api_client.post(
            reverse("contact-list"), {"contact": juan.pk}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED  # idempotente
        assert Contact.objects.count() == 2  # solo el par espejo

    def test_list_scoped(self, api_client, user, juan, maria):
        add_contact(user, juan)
        add_contact(juan, maria)
        response = api_client.get(reverse("contact-list"))
        results = response.data.get("results", response.data)
        emails = [r["contact_email"] for r in results]
        assert emails == ["juan@example.com"]

    def test_destroy_removes_both(self, api_client, user, juan):
        add_contact(user, juan)
        row = Contact.objects.get(user=user, contact=juan)
        response = api_client.delete(
            reverse("contact-detail", kwargs={"pk": row.pk})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Contact.objects.count() == 0

    def test_search_action(self, api_client, user, juan):
        response = api_client.get(reverse("contact-search"), {"q": "juan"})
        assert response.status_code == status.HTTP_200_OK
        emails = [r["email"] for r in response.data["results"]]
        assert "juan@example.com" in emails


# ── ContactGroup (Grupos) tests ─────────────────────────────


@pytest.fixture
def contact_juan(user, juan):
    add_contact(user, juan)
    return Contact.objects.get(user=user, contact=juan)


@pytest.fixture
def contact_maria(user, maria):
    add_contact(user, maria)
    return Contact.objects.get(user=user, contact=maria)


@pytest.mark.django_db
class TestContactGroupModel:
    def test_unique_name_per_user(self, user):
        ContactGroup.objects.create(user=user, name="Familia")
        with pytest.raises(Exception):
            ContactGroup.objects.create(user=user, name="Familia")

    def test_same_name_other_user_allowed(self, user, juan):
        ContactGroup.objects.create(user=user, name="Familia")
        assert ContactGroup.objects.create(user=juan, name="Familia")

    def test_member_cannot_repeat_in_group(self, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        group.members.add(contact_juan)  # idempotente, no duplica
        assert group.members.count() == 1

    def test_removing_contact_removes_from_groups(self, user, juan, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        remove_contact(user, juan)
        group.refresh_from_db()
        assert group.members.count() == 0
        assert ContactGroup.objects.filter(pk=group.pk).exists()  # el grupo queda

    def test_deleting_group_keeps_contacts(self, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        group.delete()
        assert Contact.objects.filter(pk=contact_juan.pk).exists()


@pytest.mark.django_db
class TestContactGroupWebViews:
    def test_group_list_shows_member_count(self, client, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje Cartagena")
        group.members.add(contact_juan)
        client.force_login(user)
        response = client.get(reverse("core:group_list"))
        assert response.status_code == 200
        assert b"Viaje Cartagena" in response.content

    def test_create_group_with_members(self, client, user, contact_juan, contact_maria):
        client.force_login(user)
        response = client.post(
            reverse("core:group_create"),
            {
                "name": "Viaje",
                "description": "Gastos del viaje",
                "members": [contact_juan.pk, contact_maria.pk],
            },
        )
        assert response.status_code == 302
        group = ContactGroup.objects.get(user=user, name="Viaje")
        assert group.members.count() == 2

    def test_cannot_add_foreign_contact(self, client, user, juan, maria):
        # Relación de otro usuario: juan→maria no es contacto de `user`.
        add_contact(juan, maria)
        foreign_row = Contact.objects.get(user=juan, contact=maria)
        client.force_login(user)
        response = client.post(
            reverse("core:group_create"),
            {"name": "Viaje", "members": [foreign_row.pk]},
        )
        assert response.status_code == 200  # re-render con error
        assert not ContactGroup.objects.filter(user=user, name="Viaje").exists()

    def test_duplicate_name_blocked(self, client, user):
        ContactGroup.objects.create(user=user, name="Familia")
        client.force_login(user)
        response = client.post(
            reverse("core:group_create"), {"name": "Familia"}
        )
        assert response.status_code == 200  # re-render con error
        assert ContactGroup.objects.filter(user=user, name="Familia").count() == 1

    def test_update_members_without_recreating(
        self, client, user, contact_juan, contact_maria
    ):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        client.force_login(user)
        response = client.post(
            reverse("core:group_update", kwargs={"pk": group.pk}),
            {"name": "Viaje", "description": "", "members": [contact_maria.pk]},
        )
        assert response.status_code == 302
        group.refresh_from_db()
        members = list(group.members.all())
        assert members == [contact_maria]

    def test_group_detail_lists_members(self, client, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        client.force_login(user)
        response = client.get(reverse("core:group_detail", kwargs={"pk": group.pk}))
        assert response.status_code == 200
        assert b"juan@example.com" in response.content

    def test_delete_group_web(self, client, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        client.force_login(user)
        response = client.post(reverse("core:group_delete", kwargs={"pk": group.pk}))
        assert response.status_code == 302
        assert not ContactGroup.objects.filter(pk=group.pk).exists()
        assert Contact.objects.filter(pk=contact_juan.pk).exists()

    def test_group_list_scoped(self, client, user, juan):
        ContactGroup.objects.create(user=juan, name="Ajeno")
        client.force_login(user)
        response = client.get(reverse("core:group_list"))
        assert b"Ajeno" not in response.content


@pytest.mark.django_db
class TestContactGroupAPI:
    def test_create_group_with_members(self, api_client, user, contact_juan):
        response = api_client.post(
            reverse("contact-group-list"),
            {"name": "Viaje API", "members": [contact_juan.pk]},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["member_count"] == 1
        assert response.data["members_detail"][0]["email"] == "juan@example.com"

    def test_foreign_contact_rejected(self, api_client, user, juan, maria):
        add_contact(juan, maria)
        foreign_row = Contact.objects.get(user=juan, contact=maria)
        response = api_client.post(
            reverse("contact-group-list"),
            {"name": "Viaje", "members": [foreign_row.pk]},
            format="json",
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_duplicate_name_rejected(self, api_client, user):
        ContactGroup.objects.create(user=user, name="Familia")
        response = api_client.post(
            reverse("contact-group-list"), {"name": "Familia"}, format="json"
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_list_scoped(self, api_client, user, juan):
        ContactGroup.objects.create(user=user, name="Mío")
        ContactGroup.objects.create(user=juan, name="Ajeno")
        response = api_client.get(reverse("contact-group-list"))
        results = response.data.get("results", response.data)
        names = [g["name"] for g in results]
        assert names == ["Mío"]

    def test_update_members(self, api_client, user, contact_juan, contact_maria):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        response = api_client.patch(
            reverse("contact-group-detail", kwargs={"pk": group.pk}),
            {"members": [contact_maria.pk]},
            format="json",
        )
        assert response.status_code == status.HTTP_200_OK
        group.refresh_from_db()
        assert list(group.members.all()) == [contact_maria]

    def test_delete_group_keeps_contacts(self, api_client, user, contact_juan):
        group = ContactGroup.objects.create(user=user, name="Viaje")
        group.members.add(contact_juan)
        response = api_client.delete(
            reverse("contact-group-detail", kwargs={"pk": group.pk})
        )
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Contact.objects.filter(pk=contact_juan.pk).exists()
