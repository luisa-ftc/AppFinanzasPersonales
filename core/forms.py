"""Formularios de la interfaz web clásica de FinTrack.

Incluye autenticación (registro/login por email) y los formularios CRUD de
cuentas, categorías, presupuestos y transacciones, varios de los cuales
acotan sus querysets al usuario autenticado para no exponer datos de otros
usuarios en los selects.
"""

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from core.models import (
    Account,
    AccountCreditCardDetails,
    Attachment,
    Budget,
    Category,
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


class TransactionForm(forms.ModelForm):
    """Formulario CRUD de transacciones (ingreso, gasto o transferencia)."""

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
        """Acota cuentas, categorías y etiquetas seleccionables a las del `user` recibido."""
        super().__init__(*args, **kwargs)
        self.fields["account"].queryset = Account.objects.filter(user=user, is_active=True)
        self.fields["category"].queryset = Category.objects.filter(user=user, is_active=True)
        self.fields["transfer_to_account"].queryset = Account.objects.filter(
            user=user, is_active=True
        )
        self.fields["tags"].queryset = Tag.objects.filter(user=user)


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
