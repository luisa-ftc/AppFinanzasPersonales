"""Serializers de la API REST de FinTrack.

Todos siguen el mismo patrón: asignan `user` desde `self.context["request"]`
en `create` (nunca lo aceptan como campo de entrada) y, cuando referencian
otros modelos del usuario (cuenta, categoría, etiquetas), acotan esos
querysets al usuario autenticado en `__init__` para no exponer ni permitir
asociar datos de otros usuarios.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework import serializers

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
    SharedExpensePayment,
    Tag,
    Transaction,
)
from core.services.accounts import calculate_account_balance
from core.services.credit_cards import (
    get_available_credit,
    get_next_payment_due_date,
    get_next_statement_date,
    get_used_credit,
)
from core.services.goals import (
    validate_expense_against_goal,
    validate_income_against_goal,
)
from core.services.shared_expenses import (
    build_participant_specs,
    resolve_participants,
    validate_payer_is_participant,
    validate_payment_against_participant,
)

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Representación de solo lectura del usuario autenticado (usado por `/api/auth/me/`)."""

    class Meta:
        model = User
        fields = ("id", "email", "username", "first_name", "last_name", "date_joined")
        read_only_fields = ("id", "date_joined")


class RegisterSerializer(serializers.ModelSerializer):
    """Registro de usuarios vía API, con confirmación de contraseña y hasheo seguro."""

    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("email", "username", "password", "password_confirm", "first_name", "last_name")

    def validate(self, data):
        """Verifica que `password` y `password_confirm` coincidan."""
        if data["password"] != data["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Las contraseñas no coinciden."})
        return data

    def create(self, validated_data):
        """Crea el usuario aplicando `set_password` para almacenar el hash, no el texto plano."""
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class AccountCreditCardDetailsSerializer(serializers.ModelSerializer):
    """Serializer de los campos específicos de una cuenta de tipo tarjeta de crédito."""

    class Meta:
        model = AccountCreditCardDetails
        fields = ("credit_limit", "statement_day", "payment_due_day")


class AccountSerializer(serializers.ModelSerializer):
    """Serializer de cuentas; expone `balance` como campo calculado, no persistido,
    y para tarjetas de crédito anida sus detalles y expone crédito
    usado/disponible y próximas fechas de corte/pago como campos calculados."""

    balance = serializers.SerializerMethodField()
    credit_card_details = AccountCreditCardDetailsSerializer(required=False, allow_null=True)
    used_credit = serializers.SerializerMethodField()
    available_credit = serializers.SerializerMethodField()
    next_statement_date = serializers.SerializerMethodField()
    next_payment_due_date = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = (
            "id",
            "name",
            "account_type",
            "currency",
            "initial_balance",
            "is_active",
            "balance",
            "credit_card_details",
            "used_credit",
            "available_credit",
            "next_statement_date",
            "next_payment_due_date",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "currency",
            "created_at",
            "updated_at",
            "balance",
            "used_credit",
            "available_credit",
            "next_statement_date",
            "next_payment_due_date",
        )

    def get_balance(self, obj):
        """Calcula el saldo actual de la cuenta (no es un valor almacenado en BD)."""
        return str(calculate_account_balance(obj))

    def _get_details(self, obj):
        return getattr(obj, "credit_card_details", None)

    def get_used_credit(self, obj):
        """Crédito utilizado, solo para cuentas de tipo tarjeta de crédito."""
        if obj.account_type != Account.AccountType.CREDIT:
            return None
        return str(get_used_credit(obj))

    def get_available_credit(self, obj):
        """Crédito disponible, solo para cuentas de tipo tarjeta de crédito con detalle."""
        details = self._get_details(obj)
        if obj.account_type != Account.AccountType.CREDIT or not details:
            return None
        return str(get_available_credit(obj, details))

    def get_next_statement_date(self, obj):
        """Próxima fecha de corte, solo para cuentas de tipo tarjeta de crédito con detalle."""
        details = self._get_details(obj)
        if obj.account_type != Account.AccountType.CREDIT or not details:
            return None
        return get_next_statement_date(details)

    def get_next_payment_due_date(self, obj):
        """Próxima fecha límite de pago, solo para cuentas de tipo tarjeta de crédito con detalle."""
        details = self._get_details(obj)
        if obj.account_type != Account.AccountType.CREDIT or not details:
            return None
        return get_next_payment_due_date(details)

    def create(self, validated_data):
        """Asocia la cuenta creada al usuario autenticado y, si aplica, crea su detalle de tarjeta."""
        details_data = validated_data.pop("credit_card_details", None)
        validated_data["user"] = self.context["request"].user
        account = super().create(validated_data)
        if details_data and account.account_type == Account.AccountType.CREDIT:
            AccountCreditCardDetails.objects.create(account=account, **details_data)
        return account

    def update(self, instance, validated_data):
        """Actualiza la cuenta y su detalle de tarjeta: lo crea/actualiza si el tipo
        es crédito, o lo elimina si el tipo cambió a uno sin detalle."""
        details_data = validated_data.pop("credit_card_details", None)
        account = super().update(instance, validated_data)
        if account.account_type == Account.AccountType.CREDIT and details_data:
            AccountCreditCardDetails.objects.update_or_create(
                account=account, defaults=details_data
            )
        elif account.account_type != Account.AccountType.CREDIT:
            AccountCreditCardDetails.objects.filter(account=account).delete()
        return account


class CategorySerializer(serializers.ModelSerializer):
    """Serializer de categorías de ingreso/gasto."""

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "category_type",
            "color",
            "icon",
            "parent",
            "is_active",
            "created_at",
        )
        read_only_fields = ("id", "created_at")

    def create(self, validated_data):
        """Asocia la categoría creada al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class TagSerializer(serializers.ModelSerializer):
    """Serializer de etiquetas libres para transacciones."""

    class Meta:
        model = Tag
        fields = ("id", "name", "color")
        read_only_fields = ("id",)

    def create(self, validated_data):
        """Asocia la etiqueta creada al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class AttachmentSerializer(serializers.ModelSerializer):
    """Serializer de solo lectura de adjuntos (la creación se maneja aparte, con validación de archivo)."""

    class Meta:
        model = Attachment
        fields = (
            "id",
            "file",
            "original_filename",
            "content_type",
            "size",
            "uploaded_at",
        )
        read_only_fields = ("id", "original_filename", "content_type", "size", "uploaded_at")


class TransactionSerializer(serializers.ModelSerializer):
    """Serializer de transacciones, con adjuntos anidados de solo lectura."""

    attachments = AttachmentSerializer(many=True, read_only=True)

    class Meta:
        model = Transaction
        fields = (
            "id",
            "account",
            "category",
            "transaction_type",
            "amount",
            "description",
            "date",
            "is_reconciled",
            "reconciled_at",
            "transfer_to_account",
            "debt",
            "goal",
            "tags",
            "content_hash",
            "notes",
            "attachments",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "is_reconciled",
            "reconciled_at",
            "content_hash",
            "created_at",
            "updated_at",
        )

    def __init__(self, *args, **kwargs):
        """Acota cuenta, categoría, cuenta destino, deuda, meta y etiquetas al usuario autenticado."""
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(user=user)
        self.fields["transfer_to_account"].queryset = Account.objects.filter(user=user)
        self.fields["debt"].queryset = Debt.objects.filter(user=user)
        self.fields["goal"].queryset = Goal.objects.filter(user=user)
        self.fields["tags"].queryset = Tag.objects.filter(user=user)

    def create(self, validated_data):
        """Asocia la transacción creada al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)

    def validate(self, attrs):
        """Impide asociar a deuda y meta a la vez, y valida las reglas de la meta.

        La lógica de deudas se mantiene igual que antes (validación solo en la
        capa web); aquí solo se añade la exclusión mutua y las reglas de metas.

        También bloquea editar una transacción que pertenece a un gasto
        compartido (ver `TransactionForm.clean` para el porqué)."""
        if self.instance is not None and (
            hasattr(self.instance, "shared_expense")
            or hasattr(self.instance, "shared_expense_payment")
        ):
            raise serializers.ValidationError(
                "Esta transacción pertenece a un gasto compartido; "
                "elimínalo y créalo de nuevo para modificarlo."
            )
        debt = attrs.get("debt", getattr(self.instance, "debt", None))
        goal = attrs.get("goal", getattr(self.instance, "goal", None))
        if debt and goal:
            raise serializers.ValidationError(
                "Una transacción no puede asociarse a una deuda y una meta a la vez."
            )
        tx_type = attrs.get(
            "transaction_type", getattr(self.instance, "transaction_type", None)
        )
        amount = attrs.get("amount", getattr(self.instance, "amount", None))
        if goal and amount and tx_type != "transfer":
            if tx_type == "income":
                validate_income_against_goal(goal, amount)
            elif tx_type == "expense":
                validate_expense_against_goal(goal, amount)
        return attrs


class BudgetSerializer(serializers.ModelSerializer):
    """Serializer de presupuestos; expone `spent`/`remaining`/`percent_used` como campos calculados."""

    spent = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    remaining = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    percent_used = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)

    class Meta:
        model = Budget
        fields = (
            "id",
            "category",
            "amount",
            "period_start",
            "period_end",
            "notes",
            "spent",
            "remaining",
            "percent_used",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "spent", "remaining", "percent_used")

    def __init__(self, *args, **kwargs):
        """Acota el queryset de categorías al usuario autenticado."""
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["category"].queryset = Category.objects.filter(user=user)

    def create(self, validated_data):
        """Asocia el presupuesto creado al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)

    def to_representation(self, instance):
        """Recalcula gasto/restante/porcentaje en cada representación (no son valores persistidos)."""
        data = super().to_representation(instance)
        data["spent"] = str(instance.spent)
        data["remaining"] = str(instance.remaining)
        data["percent_used"] = str(instance.percent_used)
        return data


class DebtSerializer(serializers.ModelSerializer):
    monto_pendiente = serializers.SerializerMethodField()
    estado = serializers.SerializerMethodField()
    percent_paid = serializers.SerializerMethodField()

    class Meta:
        model = Debt
        fields = (
            "id",
            "nombre",
            "prestamista",
            "monto_requerido",
            "monto_pagado",
            "fecha_limite",
            "observaciones",
            "monto_pendiente",
            "estado",
            "percent_paid",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "monto_pendiente", "estado", "percent_paid")

    def get_monto_pendiente(self, obj):
        return str(obj.monto_pendiente)

    def get_estado(self, obj):
        return str(obj.estado)

    def get_percent_paid(self, obj):
        return str(obj.percent_paid)

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class GoalSerializer(serializers.ModelSerializer):
    monto_pendiente = serializers.SerializerMethodField()
    estado = serializers.SerializerMethodField()
    percent_abonado = serializers.SerializerMethodField()

    class Meta:
        model = Goal
        fields = (
            "id",
            "nombre",
            "monto_requerido",
            "monto_abonado",
            "fecha_limite",
            "observaciones",
            "monto_pendiente",
            "estado",
            "percent_abonado",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "monto_pendiente",
            "estado",
            "percent_abonado",
        )

    def get_monto_pendiente(self, obj):
        return str(obj.monto_pendiente)

    def get_estado(self, obj):
        return str(obj.estado)

    def get_percent_abonado(self, obj):
        return str(obj.percent_abonado)

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


class ContactSerializer(serializers.ModelSerializer):
    """Serializer de contactos. `contact` es el id del usuario a agregar; la
    creación de la relación bidireccional (filas espejo) la hace el ViewSet
    vía `core.services.contacts.add_contact`, no este serializer."""

    contact_email = serializers.EmailField(source="contact.email", read_only=True)
    contact_name = serializers.SerializerMethodField()

    class Meta:
        model = Contact
        fields = (
            "id",
            "contact",
            "contact_email",
            "contact_name",
            "status",
            "created_at",
        )
        read_only_fields = ("id", "status", "created_at")

    def get_contact_name(self, obj):
        return obj.contact.get_full_name() or obj.contact.username


class ContactGroupSerializer(serializers.ModelSerializer):
    """Serializer de grupos de contactos. `members` recibe/expone ids de filas
    `Contact` del usuario (acotadas en `__init__`); `members_detail` y
    `member_count` son calculados de solo lectura."""

    members = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Contact.objects.none(), required=False
    )
    members_detail = serializers.SerializerMethodField()
    member_count = serializers.SerializerMethodField()

    class Meta:
        model = ContactGroup
        fields = (
            "id",
            "name",
            "description",
            "members",
            "members_detail",
            "member_count",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "members_detail",
            "member_count",
        )

    def __init__(self, *args, **kwargs):
        """Acota los integrantes seleccionables a los contactos del usuario."""
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["members"].child_relation.queryset = Contact.objects.filter(
            user=user
        )

    def get_members_detail(self, obj):
        return [
            {
                "id": m.pk,
                "name": m.contact.get_full_name() or m.contact.username,
                "email": m.contact.email,
            }
            for m in obj.members.select_related("contact")
        ]

    def get_member_count(self, obj):
        return obj.members.count()

    def validate_name(self, value):
        """Valida que el usuario no tenga otro grupo con el mismo nombre."""
        user = self.context["request"].user
        qs = ContactGroup.objects.filter(user=user, name=value)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("Ya tienes un grupo con ese nombre.")
        return value

    def create(self, validated_data):
        """Crea el grupo del usuario autenticado y asigna sus integrantes."""
        members = validated_data.pop("members", [])
        validated_data["user"] = self.context["request"].user
        group = ContactGroup.objects.create(**validated_data)
        group.members.set(members)
        return group

    def update(self, instance, validated_data):
        """Actualiza el grupo y, si se envían, sincroniza sus integrantes."""
        members = validated_data.pop("members", None)
        instance = super().update(instance, validated_data)
        if members is not None:
            instance.members.set(members)
        return instance


class SharedExpenseParticipantSerializer(serializers.ModelSerializer):
    """Serializer de solo lectura de un participante de un gasto compartido."""

    display_name = serializers.CharField(read_only=True)
    amount_pending = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    status = serializers.CharField(read_only=True)

    class Meta:
        model = SharedExpenseParticipant
        fields = (
            "id",
            "contact",
            "display_name",
            "is_owner",
            "is_payer",
            "amount_assigned",
            "amount_paid",
            "amount_pending",
            "status",
        )
        read_only_fields = fields


class SharedExpensePaymentSerializer(serializers.ModelSerializer):
    """Serializer para registrar un pago de un participante de un gasto compartido.

    `participant` se acota, en `__init__`, a los participantes del gasto
    compartido recibido en `context["shared_expense"]` (patrón análogo a
    `SharedExpensePaymentForm`). `account` solo es obligatoria cuando el
    dueño participa directamente en ese pago concreto (saldando su propia
    parte o recibiendo el pago de otro) — ver
    `core.services.shared_expenses.get_shared_expense_payment_transaction_type`,
    evaluado en `validate()` según el `participant` recibido, no en
    `__init__`, porque depende de quién se elija."""

    class Meta:
        model = SharedExpensePayment
        fields = (
            "id", "participant", "amount", "date", "account", "notes", "created_at",
        )
        read_only_fields = ("id", "created_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["account"].required = False
        shared_expense = self.context.get("shared_expense")
        if shared_expense is not None:
            self.fields["participant"].queryset = shared_expense.participants.all()
            self.fields["account"].queryset = Account.objects.filter(
                user=shared_expense.user, is_active=True
            )

    def validate(self, attrs):
        from core.models import Transaction as TransactionModel
        from core.services.shared_expenses import (
            get_shared_expense_payment_transaction_type,
        )

        validate_payment_against_participant(attrs["participant"], attrs["amount"])
        shared_expense = self.context.get("shared_expense")
        payer = shared_expense.payer_participant if shared_expense else None
        tx_type = get_shared_expense_payment_transaction_type(
            attrs["participant"], payer
        )
        if tx_type is not None and not attrs.get("account"):
            if tx_type == TransactionModel.TransactionType.EXPENSE:
                raise serializers.ValidationError(
                    {"account": ["Selecciona la cuenta desde la que pagaste tu parte."]}
                )
            raise serializers.ValidationError(
                {"account": ["Selecciona la cuenta donde recibiste el pago."]}
            )
        return attrs

    def create(self, validated_data):
        from core.services.shared_expenses import register_shared_expense_payment

        return register_shared_expense_payment(
            participant=validated_data["participant"],
            amount=validated_data["amount"],
            date=validated_data["date"],
            notes=validated_data.get("notes", ""),
            account=validated_data.get("account"),
        )


class SharedExpenseSerializer(serializers.ModelSerializer):
    """Serializer de gastos compartidos.

    Mezcla campos de solo-escritura sin equivalente directo en el modelo
    (`account`, `category`, `date`, `total_amount`, `contacts`, `groups`,
    `include_owner`, `payer` — igual que en `SharedExpenseForm`, porque
    `SharedExpense` expone esos datos como `@property` sobre su
    `Transaction`) con campos calculados de solo lectura. Sin `update()`:
    el módulo no soporta edición en esta versión.
    """

    account = serializers.PrimaryKeyRelatedField(
        queryset=Account.objects.none(), write_only=True, required=False
    )
    category = serializers.PrimaryKeyRelatedField(queryset=Category.objects.none(), write_only=True)
    date = serializers.DateField(write_only=True)
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, write_only=True)
    contacts = serializers.PrimaryKeyRelatedField(
        queryset=Contact.objects.none(), many=True, required=False, write_only=True
    )
    groups = serializers.PrimaryKeyRelatedField(
        queryset=ContactGroup.objects.none(), many=True, required=False, write_only=True
    )
    include_owner = serializers.BooleanField(required=False, default=True, write_only=True)
    payer = serializers.CharField(write_only=True)

    account_detail = serializers.SerializerMethodField()
    category_detail = serializers.SerializerMethodField()
    amount_recovered = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    amount_pending = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)
    percent_recovered = serializers.DecimalField(max_digits=5, decimal_places=2, read_only=True)
    estado = serializers.CharField(read_only=True)
    participant_count = serializers.IntegerField(read_only=True)
    payment_count = serializers.IntegerField(read_only=True)
    participants = SharedExpenseParticipantSerializer(many=True, read_only=True)

    class Meta:
        model = SharedExpense
        fields = (
            "id",
            "name",
            "description",
            "split_method",
            "account",
            "category",
            "date",
            "total_amount",
            "contacts",
            "groups",
            "include_owner",
            "payer",
            "account_detail",
            "category_detail",
            "amount_recovered",
            "amount_pending",
            "percent_recovered",
            "estado",
            "participant_count",
            "payment_count",
            "participants",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["account"].queryset = Account.objects.filter(user=user, is_active=True)
        self.fields["category"].queryset = Category.objects.filter(
            user=user, is_active=True, category_type=Category.CategoryType.EXPENSE
        )
        self.fields["contacts"].child_relation.queryset = Contact.objects.filter(user=user)
        self.fields["groups"].child_relation.queryset = ContactGroup.objects.filter(user=user)

    def get_account_detail(self, obj):
        if obj.account is None:
            return None
        return {"id": obj.account.pk, "name": obj.account.name}

    def get_category_detail(self, obj):
        if obj.category is None:
            return None
        return {"id": obj.category.pk, "name": obj.category.name}

    def _resolve_payer_spec(self, raw_payer, specs, user):
        from core.services.shared_expenses import ParticipantSpec

        if raw_payer == "owner":
            return ParticipantSpec(True, None)
        kind, _, pk = (raw_payer or "").partition(":")
        if kind != "contact" or not pk:
            return None
        contact = Contact.objects.filter(user=user, pk=pk).first()
        if contact is None:
            return None
        return ParticipantSpec(False, contact)

    def validate(self, attrs):
        user = self.context["request"].user
        contacts = attrs.get("contacts", [])
        groups = attrs.get("groups", [])
        include_owner = attrs.get("include_owner", True)

        resolved = resolve_participants(
            user, contact_ids=[c.pk for c in contacts], group_ids=[g.pk for g in groups]
        )
        specs = build_participant_specs(include_owner, resolved)
        if not specs:
            raise serializers.ValidationError(
                {"non_field_errors": ["Debes seleccionar al menos un participante."]}
            )
        payer_spec = self._resolve_payer_spec(attrs.get("payer"), specs, user)
        if payer_spec is None:
            raise serializers.ValidationError({"payer": ["El pagador seleccionado no es válido."]})
        try:
            validate_payer_is_participant(payer_spec, specs)
        except ValidationError as exc:
            raise serializers.ValidationError({"payer": [exc.messages[0]]})

        if payer_spec.is_owner and not attrs.get("account"):
            raise serializers.ValidationError(
                {"account": ["Selecciona la cuenta de origen: tú pagaste este gasto."]}
            )

        attrs["_participant_specs"] = specs
        attrs["_payer_spec"] = payer_spec
        return attrs

    def create(self, validated_data):
        from core.services.shared_expenses import create_shared_expense

        return create_shared_expense(
            user=self.context["request"].user,
            name=validated_data["name"],
            description=validated_data.get("description", ""),
            account=validated_data.get("account"),
            category=validated_data["category"],
            date=validated_data["date"],
            total_amount=validated_data["total_amount"],
            participant_specs=validated_data["_participant_specs"],
            payer_spec=validated_data["_payer_spec"],
            split_method=validated_data.get("split_method", SharedExpense.SplitMethod.EQUAL),
        )


class DashboardSerializer(serializers.Serializer):
    """Forma esperada del payload del dashboard (no ligado a un modelo, ver `DashboardAPIView`)."""

    total_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    accounts = AccountSerializer(many=True)
    monthly_chart = serializers.DictField()
    category_chart = serializers.DictField()
