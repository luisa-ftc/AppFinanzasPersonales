"""Mapa de rutas de la interfaz web clásica de `core`: autenticación, dashboard
y CRUD de cuentas, categorías, presupuestos y transacciones (incluye
conciliación, adjuntos, import/export CSV y reporte PDF)."""

from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

app_name = "core"

urlpatterns = [
    # Auth
    path("register/", views.RegisterView.as_view(), name="register"),
    path("login/", views.UserLoginView.as_view(), name="login"),
    path("logout/", views.UserLogoutView.as_view(), name="logout"),
    path(
        "password-reset/",
        views.UserPasswordResetView.as_view(),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="core/auth/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="core/auth/password_reset_confirm.html",
            success_url="/login/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="core/auth/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
    # Dashboard
    path("", views.DashboardView.as_view(), name="dashboard"),
    # Accounts
    path("accounts/", views.AccountListView.as_view(), name="account_list"),
    path("accounts/new/", views.AccountCreateView.as_view(), name="account_create"),
    path("accounts/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account_update"),
    path("accounts/<int:pk>/delete/", views.AccountDeleteView.as_view(), name="account_delete"),
    # Categories
    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/new/", views.CategoryCreateView.as_view(), name="category_create"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_update"),
    path(
        "categories/<int:pk>/delete/",
        views.CategoryDeleteView.as_view(),
        name="category_delete",
    ),
    # Budgets
    path("budgets/", views.BudgetListView.as_view(), name="budget_list"),
    path("budgets/new/", views.BudgetCreateView.as_view(), name="budget_create"),
    path("budgets/<int:pk>/edit/", views.BudgetUpdateView.as_view(), name="budget_update"),
    path("budgets/<int:pk>/delete/", views.BudgetDeleteView.as_view(), name="budget_delete"),
    # Debts
    path("debts/", views.DebtListView.as_view(), name="debt_list"),
    path("debts/new/", views.DebtCreateView.as_view(), name="debt_create"),
    path("debts/<int:pk>/", views.DebtDetailView.as_view(), name="debt_detail"),
    path("debts/<int:pk>/edit/", views.DebtUpdateView.as_view(), name="debt_update"),
    path("debts/<int:pk>/delete/", views.DebtDeleteView.as_view(), name="debt_delete"),
    # Transactions
    path("transactions/", views.TransactionListView.as_view(), name="transaction_list"),
    path("transactions/new/", views.TransactionCreateView.as_view(), name="transaction_create"),
    path(
        "transactions/<int:pk>/edit/",
        views.TransactionUpdateView.as_view(),
        name="transaction_update",
    ),
    path(
        "transactions/<int:pk>/delete/",
        views.TransactionDeleteView.as_view(),
        name="transaction_delete",
    ),
    path(
        "transactions/<int:pk>/reconcile/",
        views.TransactionReconcileView.as_view(),
        name="transaction_reconcile",
    ),
    path(
        "transactions/<int:pk>/attach/",
        views.AttachmentUploadView.as_view(),
        name="transaction_attach",
    ),
    path("transactions/export/csv/", views.CSVExportView.as_view(), name="csv_export"),
    path("transactions/import/csv/", views.CSVImportView.as_view(), name="csv_import"),
    path("reports/pdf/", views.PDFReportView.as_view(), name="pdf_report"),
]
