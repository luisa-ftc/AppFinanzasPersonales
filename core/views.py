"""Vistas web clásicas (basadas en clases) de FinTrack.

Cubren autenticación, dashboard, CRUD de cuentas/categorías/presupuestos/
transacciones/deudas, conciliación, import/export CSV, reporte PDF y subida
de adjuntos. Toda la lógica de negocio (saldos, gasto de presupuestos, CSV,
reportes, deudas) se delega en `core.services`; estas vistas solo orquestan
peticiones HTTP y aíslan los datos por usuario autenticado.
"""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordResetView
from django.contrib.messages.views import SuccessMessageMixin
from django.db import transaction as db_transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    ListView,
    TemplateView,
    UpdateView,
    View,
)

from core.forms import (
    ACCOUNT_DETAIL_FORMS,
    AccountForm,
    AttachmentForm,
    BudgetForm,
    CategoryForm,
    CSVImportForm,
    DebtForm,
    LoginForm,
    RegisterForm,
    TransactionFilterForm,
    TransactionForm,
)
from core.models import Account, AccountCreditCardDetails, Attachment, Budget, Category, Debt, Transaction
from core.services.debts import (
    apply_transaction_to_debt,
    get_debt_transaction_history,
    revert_transaction_from_debt,
)
from core.services.accounts import calculate_account_balance, get_balances_by_currency, get_user_total_balance
from core.services.credit_cards import (
    get_available_credit,
    get_next_payment_due_date,
    get_next_statement_date,
)
from core.services.csv_io import export_transactions_csv, import_transactions_csv
from core.services.reports import (
    generate_transactions_pdf,
    get_category_distribution,
    get_monthly_income_expense,
)


def format_money_display(value):
    """Formatea un valor numérico como texto monetario con separador de miles y 2 decimales."""
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"


class RegisterView(SuccessMessageMixin, CreateView):
    """Registro de nuevos usuarios; redirige al dashboard si ya hay sesión iniciada."""

    form_class = RegisterForm
    template_name = "core/auth/register.html"
    success_url = reverse_lazy("core:login")
    success_message = "Cuenta creada. Inicia sesión con tu correo."

    def dispatch(self, request, *args, **kwargs):
        """Evita mostrar el formulario de registro a usuarios ya autenticados."""
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)


class UserLoginView(LoginView):
    """Login con email en vez de username, usando `LoginForm`."""

    form_class = LoginForm
    template_name = "core/auth/login.html"
    redirect_authenticated_user = True


class UserLogoutView(LogoutView):
    """Cierre de sesión, redirige a la página de login."""

    next_page = reverse_lazy("core:login")


class UserPasswordResetView(PasswordResetView):
    """Solicitud de restablecimiento de contraseña por email."""

    template_name = "core/auth/password_reset.html"
    email_template_name = "core/auth/password_reset_email.txt"
    subject_template_name = "core/auth/password_reset_subject.txt"
    success_url = reverse_lazy("core:password_reset_done")


class DashboardView(LoginRequiredMixin, TemplateView):
    """Panel principal: saldo total, saldo por cuenta activa, gráficos mensuales/por
    categoría y últimas transacciones del usuario autenticado."""

    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        """Arma el contexto del dashboard delegando los cálculos en `core.services`."""
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        accounts = Account.objects.filter(user=user, is_active=True)
        balances_by_currency = get_balances_by_currency(user)
        ctx["balances_by_currency"] = [
            {"currency": currency, "balance_display": format_money_display(balance)}
            for currency, balance in balances_by_currency.items()
        ]
        ctx["accounts"] = []
        for a in accounts:
            balance = calculate_account_balance(a)
            ctx["accounts"].append(
                {
                    "account": a,
                    "balance": balance,
                    "balance_display": format_money_display(balance),
                }
            )
        ctx["monthly_chart"] = json.dumps(get_monthly_income_expense(user))
        ctx["category_chart"] = json.dumps(get_category_distribution(user))
        recent_transactions = list(Transaction.objects.filter(user=user)[:10])
        for tx in recent_transactions:
            tx.amount_display = format_money_display(tx.amount)
        ctx["recent_transactions"] = recent_transactions
        return ctx


class UserOwnedMixin(LoginRequiredMixin):
    """Exige sesión iniciada y restringe el queryset de la vista al usuario autenticado."""

    def get_queryset(self):
        return super().get_queryset().filter(user=self.request.user)


class AccountListView(UserOwnedMixin, ListView):
    """Lista de cuentas del usuario con su saldo calculado."""

    model = Account
    template_name = "core/accounts/list.html"
    context_object_name = "accounts"

    def get_context_data(self, **kwargs):
        """Añade el saldo calculado y, para tarjetas de crédito, el detalle de cupo/fechas."""
        ctx = super().get_context_data(**kwargs)
        ctx["accounts_with_balances"] = []
        for a in ctx["accounts"]:
            balance = calculate_account_balance(a)
            credit_info = None
            details = getattr(a, "credit_card_details", None)
            if a.account_type == Account.AccountType.CREDIT and details:
                credit_info = {
                    "credit_limit_display": format_money_display(details.credit_limit),
                    "available_display": format_money_display(
                        get_available_credit(a, details)
                    ),
                    "next_statement_date": get_next_statement_date(details),
                    "next_due_date": get_next_payment_due_date(details),
                }
            ctx["accounts_with_balances"].append(
                {
                    "account": a,
                    "balance": balance,
                    "balance_display": format_money_display(balance),
                    "credit_info": credit_info,
                }
            )
        return ctx


class AccountFormMixin:
    """Mixin compartido por `AccountCreateView`/`AccountUpdateView`: gestiona el
    sub-formulario de detalles específico del tipo de cuenta elegido, usando el
    registro `ACCOUNT_DETAIL_FORMS` (core/forms.py). Agregar un tipo de cuenta
    futuro con sus propios campos no requiere tocar esta lógica, solo registrar
    su ModelForm de detalles en `ACCOUNT_DETAIL_FORMS`.
    """

    def get_detail_instance(self, account_type):
        """Devuelve la instancia de detalle existente para el tipo dado, o None."""
        account = getattr(self, "object", None)
        if not account:
            return None
        if account_type == Account.AccountType.CREDIT:
            return getattr(account, "credit_card_details", None)
        return None

    def build_detail_forms(self, data=None):
        """Instancia un formulario de detalle por cada tipo de cuenta registrado."""
        forms_by_type = {}
        for acc_type, form_cls in ACCOUNT_DETAIL_FORMS.items():
            instance = self.get_detail_instance(acc_type)
            forms_by_type[acc_type] = form_cls(data, instance=instance)
        return forms_by_type

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "detail_forms" not in kwargs:
            data = self.request.POST if self.request.method == "POST" else None
            ctx["detail_forms"] = self.build_detail_forms(data)
        return ctx

    def form_valid(self, form):
        """Asigna el usuario autenticado y guarda la cuenta junto con su
        sub-formulario de detalles (si el tipo elegido tiene uno registrado),
        todo dentro de la misma transacción atómica."""
        form.instance.user = self.request.user
        account_type = form.cleaned_data["account_type"]
        detail_form_cls = ACCOUNT_DETAIL_FORMS.get(account_type)

        detail_form = None
        if detail_form_cls:
            instance = self.get_detail_instance(account_type)
            detail_form = detail_form_cls(self.request.POST, instance=instance)
            if not detail_form.is_valid():
                detail_forms = self.build_detail_forms(self.request.POST)
                detail_forms[account_type] = detail_form
                return self.render_to_response(
                    self.get_context_data(form=form, detail_forms=detail_forms)
                )

        with db_transaction.atomic():
            response = super().form_valid(form)
            if detail_form:
                detail_form.instance.account = self.object
                detail_form.save()
            else:
                # El tipo elegido no tiene detalle propio (o cambió desde uno
                # que sí lo tenía): limpiar cualquier fila huérfana.
                AccountCreditCardDetails.objects.filter(account=self.object).delete()
        return response


class AccountCreateView(AccountFormMixin, UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Alta de una nueva cuenta, asignada automáticamente al usuario autenticado."""

    model = Account
    form_class = AccountForm
    template_name = "core/accounts/form.html"
    success_url = reverse_lazy("core:account_list")
    success_message = "Cuenta creada correctamente."


class AccountUpdateView(AccountFormMixin, UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de una cuenta existente del usuario autenticado."""

    model = Account
    form_class = AccountForm
    template_name = "core/accounts/form.html"
    success_url = reverse_lazy("core:account_list")
    success_message = "Cuenta actualizada."


class AccountDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una cuenta del usuario autenticado (borra en cascada sus transacciones)."""

    model = Account
    template_name = "core/accounts/confirm_delete.html"
    success_url = reverse_lazy("core:account_list")
    success_message = "Cuenta eliminada."


class CategoryListView(UserOwnedMixin, ListView):
    """Lista de categorías del usuario autenticado."""

    model = Category
    template_name = "core/categories/list.html"
    context_object_name = "categories"


class CategoryCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Alta de una nueva categoría, asignada automáticamente al usuario autenticado."""

    model = Category
    form_class = CategoryForm
    template_name = "core/categories/form.html"
    success_url = reverse_lazy("core:category_list")
    success_message = "Categoría creada."

    def form_valid(self, form):
        """Asigna el usuario autenticado como dueño de la categoría antes de guardar."""
        form.instance.user = self.request.user
        return super().form_valid(form)


class CategoryUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de una categoría existente del usuario autenticado."""

    model = Category
    form_class = CategoryForm
    template_name = "core/categories/form.html"
    success_url = reverse_lazy("core:category_list")
    success_message = "Categoría actualizada."


class CategoryDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una categoría del usuario autenticado."""

    model = Category
    template_name = "core/categories/confirm_delete.html"
    success_url = reverse_lazy("core:category_list")


class BudgetListView(UserOwnedMixin, ListView):
    """Lista de presupuestos del usuario con montos y avance formateados para la plantilla."""

    model = Budget
    template_name = "core/budgets/list.html"
    context_object_name = "budgets"

    def get_context_data(self, **kwargs):
        """Añade a cada presupuesto sus versiones formateadas de monto, gasto y restante."""
        ctx = super().get_context_data(**kwargs)
        for budget in ctx.get("budgets", []):
            budget.amount_display = format_money_display(budget.amount)
            budget.spent_display = format_money_display(getattr(budget, "spent", 0))
            budget.remaining_display = format_money_display(
                getattr(budget, "remaining", 0)
            )
        return ctx


class BudgetCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Alta de un presupuesto, restringiendo las categorías seleccionables a las activas del usuario."""

    model = Budget
    form_class = BudgetForm
    template_name = "core/budgets/form.html"
    success_url = reverse_lazy("core:budget_list")
    success_message = "Presupuesto creado."

    def get_form(self, form_class=None):
        """Acota el queryset de categorías a las activas del usuario autenticado."""
        form = super().get_form(form_class)
        form.fields["category"].queryset = Category.objects.filter(
            user=self.request.user, is_active=True
        )
        return form

    def form_valid(self, form):
        """Asigna el usuario autenticado como dueño del presupuesto antes de guardar."""
        form.instance.user = self.request.user
        return super().form_valid(form)


class BudgetUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de un presupuesto existente del usuario autenticado."""

    model = Budget
    form_class = BudgetForm
    template_name = "core/budgets/form.html"
    success_url = reverse_lazy("core:budget_list")
    success_message = "Presupuesto actualizado."

    def get_form(self, form_class=None):
        """Acota el queryset de categorías a las activas del usuario autenticado."""
        form = super().get_form(form_class)
        form.fields["category"].queryset = Category.objects.filter(
            user=self.request.user, is_active=True
        )
        return form


class BudgetDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de un presupuesto del usuario autenticado."""

    model = Budget
    template_name = "core/budgets/confirm_delete.html"
    success_url = reverse_lazy("core:budget_list")


class DebtListView(UserOwnedMixin, ListView):
    """Lista de deudas del usuario autenticado, con montos formateados para mostrar."""

    model = Debt
    template_name = "core/debts/list.html"
    context_object_name = "debts"

    def get_context_data(self, **kwargs):
        """Agrega los montos formateados de cada deuda para mostrarlos en la tabla."""
        ctx = super().get_context_data(**kwargs)
        for debt in ctx.get("debts", []):
            debt.monto_requerido_display = format_money_display(debt.monto_requerido)
            debt.monto_pagado_display = format_money_display(debt.monto_pagado)
            debt.monto_pendiente_display = format_money_display(debt.monto_pendiente)
        return ctx


class DebtCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Creación de una deuda para el usuario autenticado."""

    model = Debt
    form_class = DebtForm
    template_name = "core/debts/form.html"
    success_url = reverse_lazy("core:debt_list")
    success_message = "Deuda registrada."

    def form_valid(self, form):
        """Asigna el usuario autenticado como dueño de la deuda antes de guardar."""
        form.instance.user = self.request.user
        return super().form_valid(form)


class DebtUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de una deuda existente del usuario autenticado."""

    model = Debt
    form_class = DebtForm
    template_name = "core/debts/form.html"
    success_url = reverse_lazy("core:debt_list")
    success_message = "Deuda actualizada."


class DebtDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una deuda del usuario autenticado (las transacciones asociadas
    conservan su historial vía `SET_NULL` en `Transaction.debt`)."""

    model = Debt
    template_name = "core/debts/confirm_delete.html"
    success_url = reverse_lazy("core:debt_list")


class DebtDetailView(UserOwnedMixin, DetailView):
    """Detalle de una deuda con su historial de transacciones asociadas."""

    model = Debt
    template_name = "core/debts/detail.html"
    context_object_name = "debt"

    def get_context_data(self, **kwargs):
        """Agrega montos formateados de la deuda y su historial de transacciones asociadas."""
        ctx = super().get_context_data(**kwargs)
        debt = self.object
        debt.monto_requerido_display = format_money_display(debt.monto_requerido)
        debt.monto_pagado_display = format_money_display(debt.monto_pagado)
        debt.monto_pendiente_display = format_money_display(debt.monto_pendiente)
        transactions = get_debt_transaction_history(debt)
        for tx in transactions:
            tx.amount_display = format_money_display(tx.amount)
        ctx["transactions"] = transactions
        return ctx


class TransactionListView(UserOwnedMixin, ListView):
    """Lista paginada de transacciones del usuario, filtrable por `TransactionFilterForm`."""

    model = Transaction
    template_name = "core/transactions/list.html"
    context_object_name = "transactions"
    paginate_by = 20

    def get_queryset(self):
        """Aplica los filtros de búsqueda/cuenta/categoría/tipo/fecha/conciliación del formulario GET."""
        qs = super().get_queryset().select_related("account", "category")
        form = TransactionFilterForm(self.request.user, self.request.GET)
        if form.is_valid():
            if form.cleaned_data.get("q"):
                qs = qs.filter(
                    Q(description__icontains=form.cleaned_data["q"])
                    | Q(notes__icontains=form.cleaned_data["q"])
                )
            if form.cleaned_data.get("account"):
                qs = qs.filter(account=form.cleaned_data["account"])
            if form.cleaned_data.get("category"):
                qs = qs.filter(category=form.cleaned_data["category"])
            if form.cleaned_data.get("transaction_type"):
                qs = qs.filter(transaction_type=form.cleaned_data["transaction_type"])
            if form.cleaned_data.get("date_from"):
                qs = qs.filter(date__gte=form.cleaned_data["date_from"])
            if form.cleaned_data.get("date_to"):
                qs = qs.filter(date__lte=form.cleaned_data["date_to"])
            if form.cleaned_data.get("is_reconciled") == "1":
                qs = qs.filter(is_reconciled=True)
            elif form.cleaned_data.get("is_reconciled") == "0":
                qs = qs.filter(is_reconciled=False)
        return qs

    def get_context_data(self, **kwargs):
        """Añade el formulario de filtros y el monto formateado de cada transacción."""
        ctx = super().get_context_data(**kwargs)
        ctx["filter_form"] = TransactionFilterForm(
            self.request.user, self.request.GET
        )
        for tx in ctx.get("transactions", []):
            tx.amount_display = format_money_display(tx.amount)
        return ctx


class TransactionCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Alta de una transacción, asignada automáticamente al usuario autenticado."""

    model = Transaction
    form_class = TransactionForm
    template_name = "core/transactions/form.html"
    success_url = reverse_lazy("core:transaction_list")
    success_message = "Transacción registrada."

    def get_form(self, form_class=None):
        """Instancia el formulario acotado al usuario autenticado."""
        return TransactionForm(self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        """Guarda la transacción y aplica su efecto sobre la deuda asociada, todo en una transacción de BD."""
        form.instance.user = self.request.user
        with db_transaction.atomic():
            response = super().form_valid(form)
            apply_transaction_to_debt(self.object)
        return response


class TransactionUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de una transacción existente del usuario autenticado."""

    model = Transaction
    form_class = TransactionForm
    template_name = "core/transactions/form.html"
    success_url = reverse_lazy("core:transaction_list")
    success_message = "Transacción actualizada."

    def get_form(self, form_class=None):
        """Instancia el formulario acotado al usuario autenticado."""
        return TransactionForm(self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        """Revierte el efecto de la versión anterior sobre su deuda, guarda los cambios y
        aplica el nuevo efecto, todo en una transacción de BD."""
        with db_transaction.atomic():
            old = Transaction.objects.select_related("debt").get(pk=self.object.pk)
            revert_transaction_from_debt(old)
            response = super().form_valid(form)
            apply_transaction_to_debt(self.object)
        return response


class TransactionDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una transacción del usuario autenticado."""

    model = Transaction
    template_name = "core/transactions/confirm_delete.html"
    success_url = reverse_lazy("core:transaction_list")

    def form_valid(self, form):
        """Revierte el efecto de la transacción sobre su deuda asociada antes de eliminarla."""
        with db_transaction.atomic():
            revert_transaction_from_debt(self.object)
            return super().form_valid(form)


class TransactionReconcileView(LoginRequiredMixin, View):
    """Marca o desmarca una transacción del usuario como conciliada, según el parámetro `action`."""

    def post(self, request, pk):
        tx = get_object_or_404(Transaction, pk=pk, user=request.user)
        if request.POST.get("action") == "unreconcile":
            tx.unreconcile()
        else:
            tx.reconcile()
        return redirect("core:transaction_list")


class CSVExportView(LoginRequiredMixin, View):
    """Descarga en CSV de todas las transacciones del usuario autenticado."""

    def get(self, request):
        content = export_transactions_csv(request.user)
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="transacciones.csv"'
        return response


class CSVImportView(LoginRequiredMixin, TemplateView):
    """Importación de transacciones desde un archivo CSV subido por el usuario."""

    template_name = "core/transactions/import_csv.html"

    def get_context_data(self, **kwargs):
        """Muestra el formulario de subida vacío."""
        ctx = super().get_context_data(**kwargs)
        ctx["form"] = CSVImportForm()
        return ctx

    def post(self, request):
        """Procesa el CSV subido y muestra el resumen de creados/duplicados/errores."""
        form = CSVImportForm(request.POST, request.FILES)
        if form.is_valid():
            content = request.FILES["file"].read().decode("utf-8-sig")
            result = import_transactions_csv(request.user, content)
            return self.render_to_response(
                {"form": form, "result": result}
            )
        return self.render_to_response({"form": form})


class PDFReportView(LoginRequiredMixin, View):
    """Descarga en PDF de un reporte de transacciones del usuario autenticado."""

    def get(self, request):
        buffer = generate_transactions_pdf(request.user)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="reporte.pdf"'
        return response


class AttachmentUploadView(LoginRequiredMixin, View):
    """Sube un adjunto (comprobante) a una transacción del usuario autenticado."""

    def post(self, request, pk):
        tx = get_object_or_404(Transaction, pk=pk, user=request.user)
        form = AttachmentForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = request.FILES["file"]
            attachment = form.save(commit=False)
            attachment.transaction = tx
            attachment.original_filename = uploaded.name
            attachment.content_type = uploaded.content_type
            attachment.size = uploaded.size
            attachment.full_clean()
            attachment.save()
        return redirect("core:transaction_list")
