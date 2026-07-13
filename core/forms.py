"""Formularios de la interfaz web clásica de FinTrack.

Incluye autenticación (registro/login por email) y los formularios CRUD de
cuentas, categorías, presupuestos y transacciones, varios de los cuales
acotan sus querysets al usuario autenticado para no exponer datos de otros
usuarios en los selects.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from django.core.exceptions import ValidationError

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
    Tag,
    Transaction,
    User,
)


class RegisterForm(UserCreationForm):
    """Formulario de registro que añade el email como campo obligatorio sobre `UserCreationForm`."""

    email = forms.EmailField(required=True, label="Correo electrónico")
    first_name = forms.CharField(required=False, label="Nombre")
    last_name = forms.CharField(required=False, label="Apellido")

    class Meta:
        model = User
        fields = ("email", "username", "first_name", "last_name", "password1", "password2")

    def save(self, commit=True):
        """Guarda el usuario transfiriendo email, nombre y apellido desde los datos limpios."""
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if commit:
            user.save()
        return user


class LoginForm(AuthenticationForm):
    """Formulario de login que fuerza el campo `username` a validarse como email."""

    username = forms.EmailField(label="Correo electrónico")


class AccountForm(forms.ModelForm):
    """Formulario CRUD de cuentas financieras."""

    class Meta:
        model = Account
        fields = ("name", "account_type", "currency", "initial_balance", "is_active")
        widgets = {
            "currency": forms.TextInput(attrs={"list": "currency-datalist", "placeholder": "COP"}),
            "initial_balance": forms.NumberInput(attrs={"step": "0.01"}),
        }


class AccountCreditCardDetailsForm(forms.ModelForm):
    """Formulario de los campos específicos de una cuenta de tipo tarjeta de crédito."""

    class Meta:
        model = AccountCreditCardDetails
        fields = ("credit_limit", "statement_day", "payment_due_day")
        widgets = {
            "credit_limit": forms.NumberInput(attrs={"step": "0.01", "min": "0.01"}),
            "statement_day": forms.NumberInput(attrs={"min": "1", "max": "31"}),
            "payment_due_day": forms.NumberInput(attrs={"min": "1", "max": "31"}),
        }


# Registro extensible: account_type -> ModelForm de los campos específicos
# de ese tipo. Para agregar un tipo de cuenta nuevo con sus propios campos,
# basta con crear su ModelForm de detalles y añadir una entrada aquí; no se
# toca AccountForm ni las vistas de cuentas.
ACCOUNT_DETAIL_FORMS = {
    Account.AccountType.CREDIT: AccountCreditCardDetailsForm,
}


class CategoryForm(forms.ModelForm):
    """Formulario CRUD de categorías, con selector visual de color e icono/emoji libre."""

    color = forms.CharField(
        label="Color",
        required=False,
        widget=forms.TextInput(
            attrs={
                "type": "color",
                "class": "color-picker-input",
                "title": "Elige un color para la categoría",
            }
        ),
        help_text="Elige un color con el selector o usa una opción rápida.",
    )
    icon = forms.CharField(
        label="Icono",
        required=False,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Ej. 🛒",
                "class": "icon-input",
                "title": "Escribe un emoji o un nombre corto",
            }
        ),
        help_text="Puedes usar un emoji o un nombre corto para identificar la categoría.",
    )

    class Meta:
        model = Category
        fields = ("name", "category_type", "color", "icon", "parent", "is_active")


class BudgetForm(forms.ModelForm):
    """Formulario CRUD de presupuestos por categoría y periodo."""

    class Meta:
        model = Budget
        fields = ("category", "amount", "period_start", "period_end", "notes")
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01"}),
            "period_start": forms.DateInput(attrs={"type": "date"}),
            "period_end": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }


class DebtForm(forms.ModelForm):
    """Formulario CRUD de deudas."""

    class Meta:
        model = Debt
        fields = ("nombre", "prestamista", "monto_requerido", "monto_pagado", "fecha_limite", "observaciones")
        widgets = {
            "monto_requerido": forms.NumberInput(attrs={"step": "0.01"}),
            "monto_pagado": forms.NumberInput(attrs={"step": "0.01"}),
            "fecha_limite": forms.DateInput(attrs={"type": "date"}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }


class GoalForm(forms.ModelForm):
    """Formulario CRUD de metas de ahorro/inversión."""

    class Meta:
        model = Goal
        fields = ("nombre", "monto_requerido", "monto_abonado", "fecha_limite", "observaciones")
        widgets = {
            "monto_requerido": forms.NumberInput(attrs={"step": "0.01"}),
            "monto_abonado": forms.NumberInput(attrs={"step": "0.01"}),
            "fecha_limite": forms.DateInput(attrs={"type": "date"}),
            "observaciones": forms.Textarea(attrs={"rows": 3}),
        }


class ContactAddForm(forms.Form):
    """Formulario para agregar un contacto: recibe el id del usuario elegido
    en el buscador (el autocompletado JS de la plantilla pobla el campo oculto)."""

    contact_id = forms.IntegerField(widget=forms.HiddenInput())

    def clean_contact_id(self):
        """Resuelve el id al usuario registrado; error si no existe."""
        contact_id = self.cleaned_data["contact_id"]
        contact_user = User.objects.filter(pk=contact_id).first()
        if contact_user is None:
            raise forms.ValidationError("El usuario seleccionado no existe.")
        self.contact_user = contact_user
        return contact_id


class ContactMultipleChoiceField(forms.ModelMultipleChoiceField):
    """Selector múltiple de contactos que muestra nombre y correo del usuario
    contacto en vez del `__str__` de la fila `Contact`."""

    def label_from_instance(self, obj):
        name = obj.contact.get_full_name() or obj.contact.username
        return f"{name} ({obj.contact.email})"


class ContactGroupForm(forms.ModelForm):
    """Formulario CRUD de grupos de contactos.

    Los integrantes se acotan a los contactos del usuario; la asignación del
    M2M (con tabla intermedia explícita) la hace la vista con `members.set()`.
    """

    members = ContactMultipleChoiceField(
        queryset=Contact.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        label="Integrantes",
        help_text="Solo puedes agregar personas de tu lista de contactos.",
    )

    class Meta:
        model = ContactGroup
        fields = ("name", "description")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, user, *args, **kwargs):
        """Acota los integrantes seleccionables a los contactos del `user` recibido."""
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["members"].queryset = Contact.objects.filter(
            user=user
        ).select_related("contact")
        if self.instance.pk:
            self.fields["members"].initial = self.instance.members.all()

    def clean_name(self):
        """Valida que el usuario no tenga otro grupo con el mismo nombre."""
        name = self.cleaned_data["name"]
        qs = ContactGroup.objects.filter(user=self.user, name=name)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Ya tienes un grupo con ese nombre.")
        return name


class TransactionForm(forms.ModelForm):
    """Formulario CRUD de transacciones (ingreso, gasto o transferencia).

    El campo `asociar_a` es un desplegable único que agrupa deudas y metas
    del usuario; en `clean()` se traduce a los FK excluyentes
    `instance.debt` / `instance.goal`.
    """

    asociar_a = forms.ChoiceField(
        required=False,
        label="Asociar a",
        help_text="Opcional: asocia esta transacción a una deuda o una meta existente.",
    )

    class Meta:
        model = Transaction
        fields = (
            "account",
            "category",
            "transaction_type",
            "amount",
            "description",
            "date",
            "transfer_to_account",
            "tags",
            "notes",
        )
        widgets = {
            "amount": forms.NumberInput(attrs={"step": "0.01"}),
            "date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "tags": forms.CheckboxSelectMultiple(),
        }

    def __init__(self, user, *args, **kwargs):
        """Acota cuentas, categorías y etiquetas al usuario, y arma el desplegable «Asociar a»."""
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["account"].queryset = Account.objects.filter(user=user, is_active=True)
        self.fields["category"].queryset = Category.objects.filter(user=user, is_active=True)
        self.fields["transfer_to_account"].queryset = Account.objects.filter(
            user=user, is_active=True
        )
        self.fields["tags"].queryset = Tag.objects.filter(user=user)

        debts = Debt.objects.filter(user=user)
        goals = Goal.objects.filter(user=user)
        choices = [("", "--------- (ninguna)")]
        if debts:
            choices.append(
                ("Deudas", [(f"debt:{d.pk}", f"{d.nombre} (Deuda)") for d in debts])
            )
        if goals:
            choices.append(
                ("Metas", [(f"goal:{g.pk}", f"{g.nombre} (Meta)") for g in goals])
            )
        self.fields["asociar_a"].choices = choices

        if self.instance and self.instance.pk:
            if self.instance.debt_id:
                self.fields["asociar_a"].initial = f"debt:{self.instance.debt_id}"
            elif self.instance.goal_id:
                self.fields["asociar_a"].initial = f"goal:{self.instance.goal_id}"

    def clean(self):
        """Traduce `asociar_a` a los FK excluyentes y valida las reglas de deuda/meta."""
        from core.services.debts import validate_expense_against_debt
        from core.services.goals import (
            validate_expense_against_goal,
            validate_income_against_goal,
        )

        cleaned = super().clean()
        tx_type = cleaned.get("transaction_type")
        amount = cleaned.get("amount")
        value = cleaned.get("asociar_a") or ""

        # Reinicia ambos FK; se reasigna según la selección.
        self.instance.debt = None
        self.instance.goal = None

        # Las transferencias no se asocian a deuda ni meta.
        if tx_type == "transfer" or not value:
            return cleaned

        kind, _, pk = value.partition(":")
        if kind == "debt":
            debt = Debt.objects.filter(user=self.user, pk=pk).first()
            if debt is None:
                self.add_error("asociar_a", "Deuda no válida.")
                return cleaned
            self.instance.debt = debt
            if tx_type == "expense" and amount:
                try:
                    validate_expense_against_debt(debt, amount)
                except ValidationError as exc:
                    self.add_error("asociar_a", exc.messages[0])
        elif kind == "goal":
            goal = Goal.objects.filter(user=self.user, pk=pk).first()
            if goal is None:
                self.add_error("asociar_a", "Meta no válida.")
                return cleaned
            self.instance.goal = goal
            if amount:
                try:
                    if tx_type == "income":
                        validate_income_against_goal(goal, amount)
                    elif tx_type == "expense":
                        validate_expense_against_goal(goal, amount)
                except ValidationError as exc:
                    self.add_error("asociar_a", exc.messages[0])
        return cleaned


class TransactionFilterForm(forms.Form):
    """Formulario (no-modelo) de filtros para la lista de transacciones: búsqueda, cuenta,
    categoría, tipo, rango de fechas y estado de conciliación."""

    q = forms.CharField(required=False, label="Buscar")
    account = forms.ModelChoiceField(
        queryset=Account.objects.none(), required=False, label="Cuenta"
    )
    category = forms.ModelChoiceField(
        queryset=Category.objects.none(), required=False, label="Categoría"
    )
    transaction_type = forms.ChoiceField(
        choices=[("", "Todos")] + list(Transaction.TransactionType.choices),
        required=False,
        label="Tipo",
    )
    date_from = forms.DateField(required=False, label="Desde", widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(required=False, label="Hasta", widget=forms.DateInput(attrs={"type": "date"}))
    is_reconciled = forms.ChoiceField(
        choices=[("", "Todos"), ("1", "Conciliadas"), ("0", "Pendientes")],
        required=False,
        label="Conciliación",
    )

    def __init__(self, user, *args, **kwargs):
        """Acota los selects de cuenta y categoría a las del `user` recibido."""
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(user=user)


class CSVImportForm(forms.Form):
    """Formulario de subida de archivo CSV para importar transacciones."""

    file = forms.FileField(label="Archivo CSV")


class AttachmentForm(forms.ModelForm):
    """Formulario de subida de un adjunto (comprobante) para una transacción."""

    class Meta:
        model = Attachment
        fields = ("file",)
