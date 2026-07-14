"""Vistas web clásicas (basadas en clases) de FinTrack.

Cubren autenticación, dashboard, CRUD de cuentas/categorías/presupuestos/
transacciones/deudas, conciliación, import/export CSV, reporte PDF y subida
de adjuntos. Toda la lógica de negocio (saldos, gasto de presupuestos, CSV,
reportes, deudas) se delega en `core.services`; estas vistas solo orquestan
peticiones HTTP y aíslan los datos por usuario autenticado.
"""

import json
from datetime import date
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import LoginView, LogoutView, PasswordResetView
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.db.models import Count, F, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import (
    CreateView,
    DeleteView,
    DetailView,
    FormView,
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
    ContactAddForm,
    ContactGroupForm,
    CSVImportForm,
    DebtForm,
    GoalForm,
    LoginForm,
    RegisterForm,
    SharedExpenseForm,
    SharedExpensePaymentForm,
    TransactionFilterForm,
    TransactionForm,
)
from core.models import (
    Account,
    AccountCreditCardDetails,
    Attachment,
    Budget,
    Category,
    Contact,
    ContactGroup,
    Debt,
    Goal,
    SharedExpense,
    SharedExpenseParticipant,
    Transaction,
)
from core.services.contacts import add_contact, remove_contact, search_users
from core.services.debts import (
    apply_transaction_to_debt,
    get_debt_transaction_history,
    revert_transaction_from_debt,
)
from core.services.goals import (
    apply_transaction_to_goal,
    get_goal_transaction_history,
    revert_transaction_from_goal,
)
from core.services.shared_expenses import (
    create_shared_expense,
    delete_shared_expense,
    register_shared_expense_payment,
    revert_shared_expense_payment_transaction,
)
from core.services.accounts import calculate_account_balance, get_user_total_balance
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
        ctx["total_balance_display"] = format_money_display(get_user_total_balance(user))
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

        today = date.today()
        active_budgets = list(
            Budget.objects.filter(
                user=user, period_start__lte=today, period_end__gte=today
            ).select_related("category")
        )
        budget_amount = sum((budget.amount for budget in active_budgets), Decimal("0"))
        budget_spent = sum((budget.spent for budget in active_budgets), Decimal("0"))
        ctx["active_budget_count"] = len(active_budgets)
        ctx["budget_amount_display"] = format_money_display(budget_amount)
        ctx["budget_spent_display"] = format_money_display(budget_spent)
        ctx["budget_percent_used"] = min(
            int((budget_spent / budget_amount) * 100) if budget_amount else 0, 100
        )

        open_debts = list(
            Debt.objects.filter(user=user).exclude(monto_pagado__gte=F("monto_requerido"))
        )
        debt_pending = sum((debt.monto_pendiente for debt in open_debts), Decimal("0"))
        ctx["open_debt_count"] = len(open_debts)
        ctx["debt_pending_display"] = format_money_display(debt_pending)

        active_goals = list(
            Goal.objects.filter(user=user).exclude(
                monto_abonado__gte=F("monto_requerido")
            )
        )
        ctx["active_goal_count"] = len(active_goals)
        ctx["goal_average_progress"] = (
            int(sum((goal.percent_abonado for goal in active_goals), Decimal("0")) / len(active_goals))
            if active_goals
            else 0
        )

        all_shared_expenses = list(
            SharedExpense.objects.filter(user=user).prefetch_related("participants")
        )
        ctx["active_shared_expense_count"] = sum(
            1 for se in all_shared_expenses
            if se.estado != SharedExpense.SharedExpenseStatus.COMPLETADO
        )
        # "Pendiente por recuperar" solo cuenta gastos donde el dueño pagó
        # (is_owner=True, is_payer=True): si pagó un contacto, el pendiente
        # no es dinero a favor del dueño.
        owner_paid_pending = sum(
            (
                se.amount_pending
                for se in all_shared_expenses
                if se.payer_participant and se.payer_participant.is_owner
                and se.estado != SharedExpense.SharedExpenseStatus.COMPLETADO
            ),
            Decimal("0"),
        )
        ctx["shared_expense_pending_display"] = format_money_display(owner_paid_pending)
        ctx["shared_expense_debtor_count"] = (
            SharedExpenseParticipant.objects.filter(shared_expense__user=user, is_owner=False)
            .filter(amount_paid__lt=F("amount_assigned"))
            .values_list("contact_id", flat=True)
            .distinct()
            .count()
        )
        recent_shared_expenses = list(
            SharedExpense.objects.filter(user=user)
            .select_related("transaction")
            .prefetch_related("participants")
            .order_by("-transaction__date")[:5]
        )
        for se in recent_shared_expenses:
            se.total_amount_display = format_money_display(se.total_amount)
            se.amount_pending_display = format_money_display(se.amount_pending)
        ctx["recent_shared_expenses"] = recent_shared_expenses

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


class GoalListView(UserOwnedMixin, ListView):
    """Lista de metas del usuario autenticado, con montos formateados para mostrar."""

    model = Goal
    template_name = "core/goals/list.html"
    context_object_name = "goals"

    def get_context_data(self, **kwargs):
        """Agrega los montos formateados de cada meta para mostrarlos en la tabla."""
        ctx = super().get_context_data(**kwargs)
        for goal in ctx.get("goals", []):
            goal.monto_requerido_display = format_money_display(goal.monto_requerido)
            goal.monto_abonado_display = format_money_display(goal.monto_abonado)
            goal.monto_pendiente_display = format_money_display(goal.monto_pendiente)
        return ctx


class GoalCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Creación de una meta para el usuario autenticado."""

    model = Goal
    form_class = GoalForm
    template_name = "core/goals/form.html"
    success_url = reverse_lazy("core:goal_list")
    success_message = "Meta registrada."

    def form_valid(self, form):
        """Asigna el usuario autenticado como dueño de la meta antes de guardar."""
        form.instance.user = self.request.user
        return super().form_valid(form)


class GoalUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de una meta existente del usuario autenticado."""

    model = Goal
    form_class = GoalForm
    template_name = "core/goals/form.html"
    success_url = reverse_lazy("core:goal_list")
    success_message = "Meta actualizada."


class GoalDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una meta del usuario autenticado (las transacciones asociadas
    conservan su historial vía `SET_NULL` en `Transaction.goal`)."""

    model = Goal
    template_name = "core/goals/confirm_delete.html"
    success_url = reverse_lazy("core:goal_list")


class GoalDetailView(UserOwnedMixin, DetailView):
    """Detalle de una meta con su historial de transacciones asociadas."""

    model = Goal
    template_name = "core/goals/detail.html"
    context_object_name = "goal"

    def get_context_data(self, **kwargs):
        """Agrega montos formateados de la meta y su historial de transacciones asociadas."""
        ctx = super().get_context_data(**kwargs)
        goal = self.object
        goal.monto_requerido_display = format_money_display(goal.monto_requerido)
        goal.monto_abonado_display = format_money_display(goal.monto_abonado)
        goal.monto_pendiente_display = format_money_display(goal.monto_pendiente)
        transactions = get_goal_transaction_history(goal)
        for tx in transactions:
            tx.amount_display = format_money_display(tx.amount)
        ctx["transactions"] = transactions
        return ctx


class ContactListView(UserOwnedMixin, ListView):
    """Lista de contactos del usuario autenticado."""

    model = Contact
    template_name = "core/contacts/list.html"
    context_object_name = "contacts"

    def get_queryset(self):
        """Trae de una vez el usuario contacto para evitar N+1 en la tabla."""
        return super().get_queryset().select_related("contact")


class ContactAddView(LoginRequiredMixin, SuccessMessageMixin, FormView):
    """Agrega un contacto buscando entre los usuarios registrados.

    El formulario recibe el id elegido en el buscador; la creación de la
    relación bidireccional se delega en `core.services.contacts.add_contact`.
    """

    form_class = ContactAddForm
    template_name = "core/contacts/form.html"
    success_url = reverse_lazy("core:contact_list")
    success_message = "Contacto agregado."

    def form_valid(self, form):
        """Crea la relación espejo; muestra el error de negocio si no es válida."""
        try:
            add_contact(self.request.user, form.contact_user)
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        return super().form_valid(form)


class ContactDetailView(UserOwnedMixin, DetailView):
    """Detalle de un contacto del usuario autenticado."""

    model = Contact
    template_name = "core/contacts/detail.html"
    context_object_name = "contact_rel"

    def get_queryset(self):
        return super().get_queryset().select_related("contact")


class ContactDeleteView(UserOwnedMixin, DeleteView):
    """Elimina la relación de contacto en ambas direcciones (no elimina al usuario)."""

    model = Contact
    template_name = "core/contacts/confirm_delete.html"
    success_url = reverse_lazy("core:contact_list")

    def form_valid(self, form):
        """Borra las dos filas espejo vía el servicio, en vez del delete simple."""
        remove_contact(self.object.user, self.object.contact)
        return redirect(self.success_url)


class ContactSearchView(LoginRequiredMixin, View):
    """Búsqueda de usuarios registrados por correo (JSON) para el autocompletado
    del formulario de agregar contacto."""

    def get(self, request):
        users = search_users(request.user, request.GET.get("q", ""))
        return JsonResponse(
            {
                "results": [
                    {
                        "id": u.pk,
                        "name": u.get_full_name() or u.username,
                        "email": u.email,
                    }
                    for u in users
                ]
            }
        )


class ContactGroupListView(UserOwnedMixin, ListView):
    """Lista de grupos de contactos del usuario, con número de integrantes."""

    model = ContactGroup
    template_name = "core/contacts/groups/list.html"
    context_object_name = "groups"

    def get_queryset(self):
        """Anota el número de integrantes para mostrarlo sin N+1."""
        return super().get_queryset().annotate(member_count=Count("members"))


class ContactGroupCreateView(UserOwnedMixin, SuccessMessageMixin, CreateView):
    """Creación de un grupo de contactos con sus integrantes."""

    model = ContactGroup
    form_class = ContactGroupForm
    template_name = "core/contacts/groups/form.html"
    success_url = reverse_lazy("core:group_list")
    success_message = "Grupo creado."

    def get_form(self, form_class=None):
        """Instancia el formulario acotado al usuario autenticado."""
        return ContactGroupForm(self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        """Asigna el dueño, guarda el grupo y sincroniza sus integrantes."""
        form.instance.user = self.request.user
        with db_transaction.atomic():
            response = super().form_valid(form)
            self.object.members.set(form.cleaned_data["members"])
        return response


class ContactGroupUpdateView(UserOwnedMixin, SuccessMessageMixin, UpdateView):
    """Edición de un grupo: nombre, descripción e integrantes, sin recrearlo."""

    model = ContactGroup
    form_class = ContactGroupForm
    template_name = "core/contacts/groups/form.html"
    success_url = reverse_lazy("core:group_list")
    success_message = "Grupo actualizado."

    def get_form(self, form_class=None):
        """Instancia el formulario acotado al usuario autenticado."""
        return ContactGroupForm(self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        """Guarda los cambios y sincroniza los integrantes con la selección."""
        with db_transaction.atomic():
            response = super().form_valid(form)
            self.object.members.set(form.cleaned_data["members"])
        return response


class ContactGroupDetailView(UserOwnedMixin, DetailView):
    """Detalle de un grupo con la lista de sus integrantes."""

    model = ContactGroup
    template_name = "core/contacts/groups/detail.html"
    context_object_name = "group"

    def get_context_data(self, **kwargs):
        """Agrega los integrantes con su usuario contacto ya cargado."""
        ctx = super().get_context_data(**kwargs)
        ctx["members"] = self.object.members.select_related("contact")
        return ctx


class ContactGroupDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de un grupo (los contactos no se tocan, solo las pertenencias)."""

    model = ContactGroup
    template_name = "core/contacts/groups/confirm_delete.html"
    success_url = reverse_lazy("core:group_list")


class SharedExpenseListView(UserOwnedMixin, ListView):
    """Lista de gastos compartidos del usuario autenticado."""

    model = SharedExpense
    template_name = "core/shared_expenses/list.html"
    context_object_name = "shared_expenses"

    def get_queryset(self):
        """Trae de una vez la transacción y los participantes para evitar N+1 en la tabla."""
        return (
            super()
            .get_queryset()
            .select_related("transaction__account", "transaction__category")
            .prefetch_related("participants__contact__contact")
        )

    def get_context_data(self, **kwargs):
        """Agrega los montos formateados de cada gasto para mostrarlos en la tabla."""
        ctx = super().get_context_data(**kwargs)
        for se in ctx.get("shared_expenses", []):
            se.total_amount_display = format_money_display(se.total_amount)
            se.amount_recovered_display = format_money_display(se.amount_recovered)
            se.amount_pending_display = format_money_display(se.amount_pending)
        return ctx


class SharedExpenseCreateView(LoginRequiredMixin, SuccessMessageMixin, FormView):
    """Creación de un gasto compartido: la construcción real (Transacción +
    participantes + reparto) la hace `create_shared_expense`, no `form.save()`."""

    form_class = SharedExpenseForm
    template_name = "core/shared_expenses/form.html"
    success_message = "Gasto compartido registrado."

    def get_form(self, form_class=None):
        """Instancia el formulario acotado al usuario autenticado."""
        return SharedExpenseForm(self.request.user, **self.get_form_kwargs())

    def form_valid(self, form):
        """Crea el gasto compartido vía el servicio; muestra el error de negocio si no es válido."""
        try:
            self.shared_expense = create_shared_expense(
                user=self.request.user,
                name=form.cleaned_data["name"],
                description=form.cleaned_data["description"],
                account=form.cleaned_data["account"],
                category=form.cleaned_data["category"],
                date=form.cleaned_data["date"],
                total_amount=form.cleaned_data["total_amount"],
                participant_specs=form.participant_specs,
                payer_spec=form.payer_spec,
                split_method=form.cleaned_data["split_method"],
            )
        except ValidationError as exc:
            form.add_error(None, exc.messages[0])
            return self.form_invalid(form)
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("core:shared_expense_detail", kwargs={"pk": self.shared_expense.pk})


class SharedExpenseDetailView(UserOwnedMixin, DetailView):
    """Detalle de un gasto compartido: resumen, participantes e historial de pagos."""

    model = SharedExpense
    template_name = "core/shared_expenses/detail.html"
    context_object_name = "shared_expense"

    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("transaction__account", "transaction__category")
            .prefetch_related("participants__contact__contact", "participants__payments")
        )

    def get_context_data(self, **kwargs):
        """Agrega montos formateados del gasto y de cada participante."""
        ctx = super().get_context_data(**kwargs)
        se = self.object
        se.total_amount_display = format_money_display(se.total_amount)
        se.amount_recovered_display = format_money_display(se.amount_recovered)
        se.amount_pending_display = format_money_display(se.amount_pending)
        participants = list(se.participants.all())
        for p in participants:
            p.amount_assigned_display = format_money_display(p.amount_assigned)
            p.amount_paid_display = format_money_display(p.amount_paid)
            p.amount_pending_display = format_money_display(p.amount_pending)
        ctx["participants"] = participants
        payments = sorted(
            (payment for p in participants for payment in p.payments.all()),
            key=lambda payment: (payment.date, payment.created_at),
            reverse=True,
        )
        for payment in payments:
            payment.amount_display = format_money_display(payment.amount)
        ctx["payments"] = payments
        return ctx


class SharedExpensePaymentCreateView(LoginRequiredMixin, SuccessMessageMixin, FormView):
    """Registra un pago recibido de un participante de un gasto compartido."""

    form_class = SharedExpensePaymentForm
    template_name = "core/shared_expenses/payment_form.html"
    success_message = "Pago registrado."

    def dispatch(self, request, *args, **kwargs):
        """Resuelve el gasto compartido del usuario autenticado antes de procesar la petición."""
        self.shared_expense = get_object_or_404(
            SharedExpense, pk=kwargs["pk"], user=request.user
        )
        return super().dispatch(request, *args, **kwargs)

    def get_form(self, form_class=None):
        """Instancia el formulario acotado a los participantes de este gasto."""
        return SharedExpensePaymentForm(self.shared_expense, **self.get_form_kwargs())

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["shared_expense"] = self.shared_expense
        return ctx

    def form_valid(self, form):
        """Registra el pago vía el servicio; genera transacción de ingreso solo si el dueño pagó el gasto."""
        register_shared_expense_payment(
            participant=form.cleaned_data["participant"],
            amount=form.cleaned_data["amount"],
            date=form.cleaned_data["date"],
            notes=form.cleaned_data["notes"],
            account=form.cleaned_data.get("account"),
        )
        return super().form_valid(form)

    def get_success_url(self):
        return reverse_lazy("core:shared_expense_detail", kwargs={"pk": self.shared_expense.pk})


class SharedExpenseDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de un gasto compartido: borra también su Transacción asociada."""

    model = SharedExpense
    template_name = "core/shared_expenses/confirm_delete.html"
    success_url = reverse_lazy("core:shared_expense_list")

    def form_valid(self, form):
        """Elimina vía el servicio (borra la Transacción, que cascada el resto)."""
        delete_shared_expense(self.object)
        return redirect(self.success_url)


class TransactionListView(UserOwnedMixin, ListView):
    """Lista paginada de transacciones del usuario, filtrable por `TransactionFilterForm`."""

    model = Transaction
    template_name = "core/transactions/list.html"
    context_object_name = "transactions"
    paginate_by = 20

    def get_queryset(self):
        """Aplica los filtros de búsqueda/cuenta/categoría/tipo/fecha/conciliación del formulario GET."""
        qs = super().get_queryset().select_related(
            "account", "category", "shared_expense", "shared_expense_payment"
        )
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
        """Guarda la transacción y aplica su efecto sobre la deuda/meta asociada, todo en una transacción de BD."""
        form.instance.user = self.request.user
        with db_transaction.atomic():
            response = super().form_valid(form)
            apply_transaction_to_debt(self.object)
            apply_transaction_to_goal(self.object)
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
        """Revierte el efecto de la versión anterior sobre su deuda/meta, guarda los cambios y
        aplica el nuevo efecto, todo en una transacción de BD."""
        with db_transaction.atomic():
            old = Transaction.objects.select_related("debt", "goal").get(pk=self.object.pk)
            revert_transaction_from_debt(old)
            revert_transaction_from_goal(old)
            response = super().form_valid(form)
            apply_transaction_to_debt(self.object)
            apply_transaction_to_goal(self.object)
        return response


class TransactionDeleteView(UserOwnedMixin, DeleteView):
    """Eliminación de una transacción del usuario autenticado."""

    model = Transaction
    template_name = "core/transactions/confirm_delete.html"
    success_url = reverse_lazy("core:transaction_list")

    def form_valid(self, form):
        """Revierte el efecto de la transacción sobre su deuda/meta/pago de gasto
        compartido asociado antes de eliminarla."""
        with db_transaction.atomic():
            revert_transaction_from_debt(self.object)
            revert_transaction_from_goal(self.object)
            revert_shared_expense_payment_transaction(self.object)
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
