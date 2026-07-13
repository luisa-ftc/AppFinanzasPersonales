"""Serializers de la API REST de FinTrack.

Todos siguen el mismo patrón: asignan `user` desde `self.context["request"]`
en `create` (nunca lo aceptan como campo de entrada) y, cuando referencian
otros modelos del usuario (cuenta, categoría, etiquetas), acotan esos
querysets al usuario autenticado en `__init__` para no exponer ni permitir
asociar datos de otros usuarios.
"""

from django.contrib.auth import get_user_model
from rest_framework import serializers

from core.models import Account, Attachment, Budget, Category, Tag, Transaction
from core.services.accounts import calculate_account_balance

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


class AccountSerializer(serializers.ModelSerializer):
    """Serializer de cuentas; expone `balance` como campo calculado, no persistido."""

    balance = serializers.SerializerMethodField()

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
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at", "balance")

    def get_balance(self, obj):
        """Calcula el saldo actual de la cuenta (no es un valor almacenado en BD)."""
        return str(calculate_account_balance(obj))

    def create(self, validated_data):
        """Asocia la cuenta creada al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


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
        """Acota cuenta, categoría, cuenta destino y etiquetas al usuario autenticado."""
        super().__init__(*args, **kwargs)
        user = self.context["request"].user
        self.fields["account"].queryset = Account.objects.filter(user=user)
        self.fields["category"].queryset = Category.objects.filter(user=user)
        self.fields["transfer_to_account"].queryset = Account.objects.filter(user=user)
        self.fields["tags"].queryset = Tag.objects.filter(user=user)

    def create(self, validated_data):
        """Asocia la transacción creada al usuario autenticado de la petición."""
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)


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


class DashboardSerializer(serializers.Serializer):
    """Forma esperada del payload del dashboard (no ligado a un modelo, ver `DashboardAPIView`)."""

    total_balance = serializers.DecimalField(max_digits=14, decimal_places=2)
    accounts = AccountSerializer(many=True)
    monthly_chart = serializers.DictField()
    category_chart = serializers.DictField()
