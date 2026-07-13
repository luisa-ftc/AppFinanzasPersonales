"""Modelos de dominio de FinTrack.

Define al usuario (autenticado por email) y las entidades financieras que
dependen de él: cuentas, categorías, etiquetas, transacciones, presupuestos,
deudas y adjuntos. Cada entidad de dominio pertenece a un único usuario
mediante una FK a `settings.AUTH_USER_MODEL`; el aislamiento entre usuarios
se aplica en las capas de vistas/API, no aquí.
"""

import hashlib
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import (
    FileExtensionValidator,
    MaxValueValidator,
    MinValueValidator,
)
from django.db import models
from django.utils import timezone


class User(AbstractUser):
    """Usuario de FinTrack que se autentica con correo electrónico en vez de username."""

    email = models.EmailField("correo electrónico", unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    class Meta:
        verbose_name = "usuario"
        verbose_name_plural = "usuarios"

    def __str__(self):
        return self.email


class Account(models.Model):
    """Cuenta financiera de un usuario (corriente, ahorros, tarjeta, efectivo o inversión).

    El saldo no se almacena: se deriva siempre del saldo inicial más el
    histórico de transacciones (ver `balance`).
    """

    class AccountType(models.TextChoices):
        CHECKING = "checking", "Cuenta corriente"
        SAVINGS = "savings", "Ahorros"
        CREDIT = "credit", "Tarjeta de crédito"
        CASH = "cash", "Efectivo"
        INVESTMENT = "investment", "Inversión"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="accounts",
    )
    name = models.CharField("nombre", max_length=100)
    account_type = models.CharField(
        "tipo",
        max_length=20,
        choices=AccountType.choices,
        default=AccountType.CHECKING,
    )
    currency = models.CharField("moneda", max_length=10, default="COP")
    initial_balance = models.DecimalField(
        "saldo inicial",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "cuenta"
        verbose_name_plural = "cuentas"
        ordering = ["name"]
        unique_together = [["user", "name"]]

    def __str__(self):
        return f"{self.name} ({self.get_account_type_display()})"

    @property
    def balance(self):
        """Saldo actual calculado (saldo inicial ± movimientos), no un valor persistido."""
        from core.services.accounts import calculate_account_balance

        return calculate_account_balance(self)


class AccountCreditCardDetails(models.Model):
    """Datos específicos de una cuenta de tipo tarjeta de crédito.

    Relación 1-a-1 con `Account`: solo existe para cuentas con
    `account_type == Account.AccountType.CREDIT`. Este es el patrón a
    replicar para futuros tipos de cuenta con campos propios (ej.
    `AccountInvestmentDetails`): un modelo de detalle separado con
    `OneToOneField` a `Account`, sin volver a modificar `Account`.
    """

    account = models.OneToOneField(
        Account,
        on_delete=models.CASCADE,
        related_name="credit_card_details",
        verbose_name="cuenta",
    )
    credit_limit = models.DecimalField(
        "cupo",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    statement_day = models.PositiveSmallIntegerField(
        "día de corte",
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )
    payment_due_day = models.PositiveSmallIntegerField(
        "día límite de pago",
        validators=[MinValueValidator(1), MaxValueValidator(31)],
    )

    class Meta:
        verbose_name = "detalle de tarjeta de crédito"
        verbose_name_plural = "detalles de tarjeta de crédito"

    def __str__(self):
        return f"Detalles tarjeta de {self.account.name}"

    def clean(self):
        """Valida que el detalle solo exista sobre una cuenta de tipo tarjeta de crédito."""
        if self.account_id and self.account.account_type != Account.AccountType.CREDIT:
            raise ValidationError(
                "Los detalles de tarjeta de crédito solo aplican a cuentas "
                "de tipo 'Tarjeta de crédito'."
            )


class Category(models.Model):
    """Categoría de ingreso o gasto de un usuario, con soporte para subcategorías (`parent`)."""

    class CategoryType(models.TextChoices):
        INCOME = "income", "Ingreso"
        EXPENSE = "expense", "Gasto"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="categories",
    )
    name = models.CharField("nombre", max_length=100)
    category_type = models.CharField(
        "tipo",
        max_length=10,
        choices=CategoryType.choices,
    )
    color = models.CharField("color", max_length=7, default="#6366f1")
    icon = models.CharField("icono", max_length=50, blank=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subcategories",
    )
    is_active = models.BooleanField("activa", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "categoría"
        verbose_name_plural = "categorías"
        ordering = ["category_type", "name"]
        unique_together = [["user", "name", "category_type"]]

    def __str__(self):
        return f"{self.name} ({self.get_category_type_display()})"


class Tag(models.Model):
    """Etiqueta libre de un usuario para clasificar transacciones (relación many-to-many)."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
    )
    name = models.CharField("nombre", max_length=50)
    color = models.CharField("color", max_length=7, default="#94a3b8")

    class Meta:
        verbose_name = "etiqueta"
        verbose_name_plural = "etiquetas"
        ordering = ["name"]
        unique_together = [["user", "name"]]

    def __str__(self):
        return self.name


class Transaction(models.Model):
    """Movimiento financiero de un usuario: ingreso, gasto o transferencia entre cuentas.

    Las transferencias usan `account` como origen y `transfer_to_account`
    como destino; ambos extremos se descuentan/suman al calcular saldos
    (ver `core.services.accounts.calculate_account_balance`).
    """

    class TransactionType(models.TextChoices):
        INCOME = "income", "Ingreso"
        EXPENSE = "expense", "Gasto"
        TRANSFER = "transfer", "Transferencia"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    account = models.ForeignKey(
        Account,
        on_delete=models.CASCADE,
        related_name="transactions",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
    )
    transaction_type = models.CharField(
        "tipo",
        max_length=10,
        choices=TransactionType.choices,
    )
    amount = models.DecimalField(
        "monto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    description = models.CharField("descripción", max_length=255)
    date = models.DateField("fecha")
    is_reconciled = models.BooleanField("conciliada", default=False)
    reconciled_at = models.DateTimeField(null=True, blank=True)
    transfer_to_account = models.ForeignKey(
        Account,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_transfers",
    )
    debt = models.ForeignKey(
        "Debt",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="deuda asociada",
    )
    goal = models.ForeignKey(
        "Goal",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transactions",
        verbose_name="meta asociada",
    )
    tags = models.ManyToManyField(Tag, blank=True, related_name="transactions")
    content_hash = models.CharField(
        "hash",
        max_length=64,
        db_index=True,
        editable=False,
    )
    notes = models.TextField("notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "transacción"
        verbose_name_plural = "transacciones"
        ordering = ["-date", "-created_at"]
        indexes = [
            models.Index(fields=["user", "date"]),
            models.Index(fields=["user", "content_hash"]),
        ]

    def __str__(self):
        return f"{self.date} - {self.description} ({self.amount})"

    @staticmethod
    def compute_hash(user_id, account_id, date, amount, description):
        """Genera un hash SHA-256 a partir de los campos que identifican una transacción.

        Se usa para detectar duplicados al importar CSV: dos filas con el
        mismo usuario, cuenta, fecha, monto y descripción (normalizada a
        minúsculas y sin espacios) producen el mismo hash, sin necesidad de
        comparar registros completos.
        """
        raw = f"{user_id}|{account_id}|{date}|{amount}|{description.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def save(self, *args, **kwargs):
        """Guarda la transacción recalculando siempre `content_hash` antes de persistir."""
        self.content_hash = self.compute_hash(
            self.user_id or self.user.pk,
            self.account_id or self.account.pk,
            self.date,
            self.amount,
            self.description,
        )
        super().save(*args, **kwargs)

    def reconcile(self):
        """Marca la transacción como conciliada y registra la fecha/hora de conciliación."""
        self.is_reconciled = True
        self.reconciled_at = timezone.now()
        self.save(update_fields=["is_reconciled", "reconciled_at", "updated_at"])

    def unreconcile(self):
        """Revierte la conciliación, limpiando el estado y la fecha de conciliación."""
        self.is_reconciled = False
        self.reconciled_at = None
        self.save(update_fields=["is_reconciled", "reconciled_at", "updated_at"])


class Budget(models.Model):
    """Presupuesto de gasto de un usuario para una categoría durante un periodo definido."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="budgets",
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="budgets",
    )
    amount = models.DecimalField(
        "monto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    period_start = models.DateField("inicio del periodo")
    period_end = models.DateField("fin del periodo")
    notes = models.TextField("notas", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "presupuesto"
        verbose_name_plural = "presupuestos"
        ordering = ["-period_start"]

    def __str__(self):
        return f"{self.category.name}: {self.amount} ({self.period_start} - {self.period_end})"

    def clean(self):
        """Valida que el periodo del presupuesto tenga fin posterior al inicio."""
        if self.period_end < self.period_start:
            raise ValidationError("La fecha fin debe ser posterior a la fecha inicio.")

    @property
    def spent(self):
        """Total gastado en la categoría del presupuesto durante su periodo (calculado, no persistido)."""
        from core.services.budgets import calculate_budget_spent

        return calculate_budget_spent(self)

    @property
    def remaining(self):
        """Monto del presupuesto que aún no se ha gastado (puede ser negativo si hay sobregasto)."""
        return self.amount - self.spent

    @property
    def percent_used(self):
        """Porcentaje del presupuesto consumido, acotado a 100 para evitar valores mayores en sobregasto."""
        if self.amount == 0:
            return Decimal("0")
        return min((self.spent / self.amount) * 100, Decimal("100"))


class Debt(models.Model):
    """Deuda o préstamo de un usuario, con seguimiento de pagos vía transacciones."""

    class DebtStatus(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PAGADA = "pagada", "Pagada"
        VENCIDA = "vencida", "Vencida"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="debts",
    )
    nombre = models.CharField("nombre", max_length=100)
    prestamista = models.CharField("prestamista", max_length=150)
    monto_requerido = models.DecimalField(
        "monto requerido",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    monto_pagado = models.DecimalField(
        "monto pagado",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    fecha_limite = models.DateField("fecha límite")
    observaciones = models.TextField("observaciones", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "deuda"
        verbose_name_plural = "deudas"
        ordering = ["-fecha_limite"]
        unique_together = [["user", "nombre"]]

    def __str__(self):
        return f"{self.nombre} ({self.prestamista})"

    @property
    def monto_pendiente(self):
        """Saldo que aún falta pagar (calculado, no persistido)."""
        return self.monto_requerido - self.monto_pagado

    @property
    def estado(self):
        """Estado derivado de la deuda: pagada si no queda saldo, vencida si pasó la fecha límite, si no pendiente."""
        if self.monto_pendiente <= 0:
            return self.DebtStatus.PAGADA
        if self.fecha_limite < timezone.now().date():
            return self.DebtStatus.VENCIDA
        return self.DebtStatus.PENDIENTE

    @property
    def estado_display(self):
        """Etiqueta legible del estado derivado (para templates)."""
        return self.DebtStatus(self.estado).label

    @property
    def percent_paid(self):
        """Porcentaje pagado de la deuda, acotado a 100."""
        if self.monto_requerido == 0:
            return Decimal("0")
        return min(
            (self.monto_pagado / self.monto_requerido) * 100, Decimal("100")
        )


class Goal(models.Model):
    """Meta de ahorro o inversión de un usuario, con seguimiento vía transacciones."""

    class GoalStatus(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        COMPLETADA = "completada", "Completada"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="goals",
    )
    nombre = models.CharField("nombre", max_length=100)
    monto_requerido = models.DecimalField(
        "monto requerido",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    monto_abonado = models.DecimalField(
        "monto abonado",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    fecha_limite = models.DateField("fecha límite")
    observaciones = models.TextField("observaciones", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "meta"
        verbose_name_plural = "metas"
        ordering = ["-fecha_limite"]
        unique_together = [["user", "nombre"]]

    def __str__(self):
        return self.nombre

    @property
    def monto_pendiente(self):
        """Monto que aún falta abonar para alcanzar el objetivo (calculado, no persistido)."""
        return self.monto_requerido - self.monto_abonado

    @property
    def estado(self):
        """Estado derivado: completada si no queda pendiente, si no pendiente."""
        if self.monto_pendiente <= 0:
            return self.GoalStatus.COMPLETADA
        return self.GoalStatus.PENDIENTE

    @property
    def estado_display(self):
        """Etiqueta legible del estado derivado (para templates)."""
        return self.GoalStatus(self.estado).label

    @property
    def percent_abonado(self):
        """Porcentaje abonado de la meta, acotado a 100."""
        if self.monto_requerido == 0:
            return Decimal("0")
        return min(
            (self.monto_abonado / self.monto_requerido) * 100, Decimal("100")
        )


def attachment_upload_path(instance, filename):
    """Ruta de almacenamiento de un adjunto, aislada por usuario y transacción."""
    return f"attachments/{instance.transaction.user_id}/{instance.transaction_id}/{filename}"


class Attachment(models.Model):
    """Archivo adjunto (comprobante) asociado a una transacción."""

    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    file = models.FileField(
        "archivo",
        upload_to=attachment_upload_path,
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "gif", "pdf"]
            )
        ],
    )
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100)
    size = models.PositiveIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "adjunto"
        verbose_name_plural = "adjuntos"
        ordering = ["-uploaded_at"]

    def __str__(self):
        return self.original_filename

    def clean(self):
        """Valida el tamaño y tipo de contenido del adjunto contra los límites de settings."""
        max_bytes = settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
        if self.size and self.size > max_bytes:
            raise ValidationError(
                f"El archivo excede el tamaño máximo de {settings.MAX_ATTACHMENT_SIZE_MB} MB."
            )
        if self.content_type and self.content_type not in settings.ALLOWED_ATTACHMENT_TYPES:
            raise ValidationError(f"Tipo de archivo no permitido: {self.content_type}")
