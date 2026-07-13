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


class Contact(models.Model):
    """Relación de contacto entre dos usuarios registrados de FinTrack.

    La relación es bidireccional y se modela con filas espejo: al agregar un
    contacto, `core.services.contacts.add_contact` crea dos filas atómicamente
    (user=A, contact=B) y (user=B, contact=A). Así la lista de cada usuario es
    un simple `filter(user=...)` que reutiliza `UserOwnedMixin`/`UserOwnedViewSet`,
    y cada fila guarda su propio `status` (preparado para estados asimétricos
    futuros: solicitud enviada/recibida, bloqueado). Nunca crear/borrar filas
    sueltas: usar siempre el servicio para no romper el espejo.
    """

    class ContactStatus(models.TextChoices):
        CONTACTO = "contacto", "Contacto"
        # Futuro: SOLICITUD_ENVIADA, SOLICITUD_RECIBIDA, BLOQUEADO

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contacts",
        verbose_name="usuario",
    )
    contact = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contact_of",
        verbose_name="contacto",
    )
    status = models.CharField(
        "estado",
        max_length=20,
        choices=ContactStatus.choices,
        default=ContactStatus.CONTACTO,
    )
    created_at = models.DateTimeField("fecha agregado", auto_now_add=True)

    class Meta:
        verbose_name = "contacto"
        verbose_name_plural = "contactos"
        ordering = ["-created_at"]
        unique_together = [["user", "contact"]]

    def __str__(self):
        return f"{self.user.email} -> {self.contact.email}"

    def clean(self):
        """Valida que un usuario no pueda agregarse a sí mismo como contacto."""
        if self.user_id and self.contact_id and self.user_id == self.contact_id:
            raise ValidationError("No puedes agregarte a ti mismo como contacto.")


class ContactGroup(models.Model):
    """Grupo de contactos de un usuario (familia, amigos, viaje, etc.).

    Los integrantes son filas `Contact` del dueño del grupo (nunca usuarios
    sueltos): así solo se puede agrupar a quien ya es contacto, y al eliminar
    la relación de contacto la BD saca al integrante de todos los grupos por
    CASCADE de la tabla intermedia, sin lógica adicional. El creador no se
    agrega a sí mismo (no es contacto propio); los módulos que consuman
    grupos (ej. Gastos Compartidos) deben tratarlo como dueño aparte.

    La relación usa la tabla intermedia explícita `ContactGroupMembership`
    para poder añadir campos por integrante a futuro (roles, invitaciones).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="contact_groups",
        verbose_name="usuario",
    )
    name = models.CharField("nombre", max_length=100)
    description = models.TextField("descripción", blank=True)
    members = models.ManyToManyField(
        Contact,
        through="ContactGroupMembership",
        related_name="groups",
        blank=True,
        verbose_name="integrantes",
    )
    created_at = models.DateTimeField("fecha de creación", auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "grupo de contactos"
        verbose_name_plural = "grupos de contactos"
        ordering = ["name"]
        unique_together = [["user", "name"]]

    def __str__(self):
        return self.name


class ContactGroupMembership(models.Model):
    """Pertenencia de un contacto a un grupo (tabla intermedia explícita).

    Hoy solo registra la fecha; es el punto de extensión para futuros campos
    por integrante (rol dentro del grupo, estado de invitación, etc.).
    """

    group = models.ForeignKey(
        ContactGroup,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="grupo",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.CASCADE,
        related_name="group_memberships",
        verbose_name="contacto",
    )
    created_at = models.DateTimeField("fecha agregado", auto_now_add=True)

    class Meta:
        verbose_name = "integrante de grupo"
        verbose_name_plural = "integrantes de grupo"
        unique_together = [["group", "contact"]]

    def __str__(self):
        return f"{self.contact.contact.email} en {self.group.name}"


class SharedExpense(models.Model):
    """Gasto pagado por el usuario (o por un contacto) y repartido entre
    varios participantes, con seguimiento de cuánto le deben devolver.

    `category`/`date`/`total_amount` son campos propios (no proxies): son
    necesarios siempre, incluso cuando no hay una `Transaction` real (ver
    abajo). Nomenclatura en inglés (a diferencia de `Debt`/`Goal`, en
    español) porque este módulo se integra directamente con
    `Transaction`/`Contact`/`ContactGroup`, que ya son en inglés.

    `account`/`transaction` son opcionales: solo se completan cuando el
    dueño de la app es quien realmente pagó (`is_owner=True` en el
    participante pagador), porque solo en ese caso salió dinero real de una
    de sus cuentas. Si pagó un contacto, el gasto es puramente informativo
    (seguimiento de reparto) y no genera ninguna `Transaction`.
    """

    class SplitMethod(models.TextChoices):
        EQUAL = "equal", "Igualitaria"
        # Futuro: PERCENTAGE = "percentage", "Por porcentajes"
        #         CUSTOM = "custom", "Montos personalizados"

    class SharedExpenseStatus(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PARCIAL = "parcial", "Parcial"
        COMPLETADO = "completado", "Completado"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shared_expenses",
    )
    name = models.CharField("nombre", max_length=150)
    description = models.TextField("descripción", blank=True)
    category = models.ForeignKey(
        "Category",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_expenses",
    )
    date = models.DateField("fecha")
    total_amount = models.DecimalField(
        "monto total",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    split_method = models.CharField(
        "método de división",
        max_length=20,
        choices=SplitMethod.choices,
        default=SplitMethod.EQUAL,
    )
    account = models.ForeignKey(
        "Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_expenses",
        verbose_name="cuenta de origen",
        help_text="Solo se completa cuando el dueño de la app es quien pagó.",
    )
    transaction = models.OneToOneField(
        "Transaction",
        on_delete=models.CASCADE,
        related_name="shared_expense",
        null=True,
        blank=True,
        editable=False,
        verbose_name="transacción de gasto asociada",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "gasto compartido"
        verbose_name_plural = "gastos compartidos"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return self.name

    @property
    def payer_participant(self):
        """Fila del participante que pagó (siempre exactamente una, tras crear el gasto)."""
        return next((p for p in self.participants.all() if p.is_payer), None)

    @property
    def amount_recovered(self):
        """Suma de lo pagado por todos los participantes (incluida la parte
        auto-saldada del pagador)."""
        return sum((p.amount_paid for p in self.participants.all()), Decimal("0.00"))

    @property
    def amount_pending(self):
        return self.total_amount - self.amount_recovered

    @property
    def percent_recovered(self):
        if self.total_amount == 0:
            return Decimal("0")
        return min(
            (self.amount_recovered / self.total_amount) * 100, Decimal("100")
        )

    @property
    def participant_count(self):
        return self.participants.count()

    @property
    def payment_count(self):
        return SharedExpensePayment.objects.filter(participant__shared_expense=self).count()

    @property
    def estado(self):
        """Estado derivado excluyendo la fila del pagador (siempre auto-saldada)
        del cómputo: solo importa si los demás participantes (deudores) han
        pagado su parte o no. Los montos agregados (`amount_recovered`/
        `amount_pending`) sí incluyen la parte del pagador."""
        debtors = [p for p in self.participants.all() if not p.is_payer]
        if not debtors:
            return self.SharedExpenseStatus.COMPLETADO
        if all(p.amount_paid <= 0 for p in debtors):
            return self.SharedExpenseStatus.PENDIENTE
        if all(p.amount_pending <= 0 for p in debtors):
            return self.SharedExpenseStatus.COMPLETADO
        return self.SharedExpenseStatus.PARCIAL

    @property
    def estado_display(self):
        return self.SharedExpenseStatus(self.estado).label


class SharedExpenseParticipant(models.Model):
    """Participación de un usuario (el dueño o un contacto) en un gasto compartido.

    El dueño se representa con `is_owner=True, contact=None` (nunca un
    `Contact`, porque un usuario no es contacto de sí mismo). Un contacto
    eliminado (`remove_contact`) deja `contact=NULL` vía `SET_NULL` pero con
    `is_owner=False`, distinguible sin ambigüedad de la fila del dueño.
    """

    class ParticipantStatus(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente"
        PARCIAL = "parcial", "Parcial"
        PAGADO = "pagado", "Pagado"

    shared_expense = models.ForeignKey(
        SharedExpense,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    contact = models.ForeignKey(
        Contact,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_expense_participations",
    )
    is_owner = models.BooleanField("es el dueño", default=False)
    is_payer = models.BooleanField("es quien pagó", default=False)
    position = models.PositiveSmallIntegerField(
        "posición",
        default=0,
        help_text="Orden de aparición usado para el reparto de céntimos sobrantes.",
    )
    amount_assigned = models.DecimalField(
        "monto asignado",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    amount_paid = models.DecimalField(
        "monto pagado",
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "participante de gasto compartido"
        verbose_name_plural = "participantes de gasto compartido"
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["shared_expense", "contact"],
                name="shx_participant_unique_contact",
            ),
            models.UniqueConstraint(
                fields=["shared_expense"],
                condition=models.Q(is_owner=True),
                name="shx_participant_unique_owner",
            ),
            models.UniqueConstraint(
                fields=["shared_expense"],
                condition=models.Q(is_payer=True),
                name="shx_participant_unique_payer",
            ),
        ]

    def __str__(self):
        return f"{self.display_name} en {self.shared_expense.name}"

    def clean(self):
        """Valida que un participante sea el dueño o un contacto, nunca ambos ni ninguno."""
        if self.is_owner and self.contact_id:
            raise ValidationError(
                "Un participante no puede ser el dueño y un contacto a la vez."
            )
        if not self.is_owner and not self.contact_id:
            raise ValidationError("Un participante debe ser el dueño o un contacto.")

    @property
    def amount_pending(self):
        return max(self.amount_assigned - self.amount_paid, Decimal("0.00"))

    @property
    def status(self):
        if self.amount_paid <= 0:
            return self.ParticipantStatus.PENDIENTE
        if self.amount_pending <= 0:
            return self.ParticipantStatus.PAGADO
        return self.ParticipantStatus.PARCIAL

    @property
    def status_display(self):
        return self.ParticipantStatus(self.status).label

    @property
    def display_name(self):
        """Nombre a mostrar: "Yo" para el dueño, el nombre del contacto, o un
        aviso si el contacto fue eliminado después de crear el gasto."""
        if self.is_owner:
            return "Yo"
        if self.contact_id:
            u = self.contact.contact
            return u.get_full_name() or u.username
        return "Contacto eliminado"


class SharedExpensePayment(models.Model):
    """Registro de que un participante saldó (total o parcialmente) su parte
    de un gasto compartido.

    El saldo vive cacheado en `SharedExpenseParticipant.amount_paid`
    (actualizado por el servicio); este modelo es el historial de auditoría
    de esos abonos. Genera una `Transaction` real **solo** cuando el dueño de
    la app participa directamente en ese pago concreto — ver
    `core.services.shared_expenses.get_shared_expense_payment_transaction_type`:
    de tipo gasto si el dueño está saldando su propia parte (pagó un
    contacto), de tipo ingreso si el dueño está cobrando (pagó él). Si el
    movimiento es entre dos contactos, sigue siendo informativo, sin
    `account`/`transaction`.
    """

    participant = models.ForeignKey(
        SharedExpenseParticipant,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(
        "monto",
        max_digits=14,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.01"))],
    )
    date = models.DateField("fecha")
    notes = models.TextField("observación", blank=True)
    account = models.ForeignKey(
        "Account",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shared_expense_payments",
        verbose_name="cuenta asociada",
        help_text="Cuenta desde donde pagaste o donde recibiste el dinero, si aplica.",
    )
    transaction = models.OneToOneField(
        "Transaction",
        on_delete=models.CASCADE,
        related_name="shared_expense_payment",
        null=True,
        blank=True,
        editable=False,
        verbose_name="transacción asociada",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pago de gasto compartido"
        verbose_name_plural = "pagos de gastos compartidos"
        ordering = ["-date", "-created_at"]

    def __str__(self):
        return f"{self.participant.display_name}: {self.amount} ({self.date})"


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
