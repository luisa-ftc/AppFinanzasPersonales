"""Suite de tests de FinTrack (pytest + pytest-django).

Cubre: configuración de seguridad, el modelo de usuario, cálculo de saldo de
cuentas, reportes por mes, detección de duplicados por hash, importación
CSV, la API de cuentas/transacciones y las vistas web de autenticación.
"""

import pytest
from copy import copy
from decimal import Decimal
from datetime import date

from django.conf import settings
from django.template import Context
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from core.admin import UserAdmin
from core.models import Account, Budget, Category, Transaction
from core.services.accounts import calculate_account_balance
from core.services.budgets import calculate_budget_spent
from core.services.csv_io import import_transactions_csv
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
