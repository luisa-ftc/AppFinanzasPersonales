"""Configuración del admin de Django para los modelos de FinTrack."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from core.models import (
    Account,
    AccountCreditCardDetails,
    Attachment,
    Budget,
    Category,
    Contact,
    ContactGroup,
    ContactGroupMembership,
    Debt,
    Goal,
    SharedExpense,
    SharedExpenseParticipant,
    SharedExpensePayment,
    Tag,
    Transaction,
    User,
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    """Admin de usuarios que reordena los `fieldsets` de Django para evitar el
    email duplicado (una vez en la sección de credenciales y otra en datos
    de contacto)."""

    list_display = ("email", "username", "first_name", "last_name", "is_staff")
    search_fields = ("email", "username", "first_name", "last_name")
    ordering = ("email",)
    fieldsets = list(BaseUserAdmin.fieldsets)
    fieldsets[1] = ("Contacto", {"fields": ("first_name", "last_name", "email")})
    fieldsets = tuple(fieldsets)
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "username", "password1", "password2"),
            },
        ),
    )


class AccountCreditCardDetailsInline(admin.StackedInline):
    model = AccountCreditCardDetails
    extra = 0
    can_delete = True


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "account_type", "currency", "is_active")
    list_filter = ("account_type", "is_active", "currency")
    search_fields = ("name", "user__email")
    inlines = [AccountCreditCardDetailsInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "category_type", "color", "is_active")
    list_filter = ("category_type", "is_active")
    search_fields = ("name", "user__email")


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "color")
    search_fields = ("name", "user__email")


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "description",
        "amount",
        "transaction_type",
        "account",
        "user",
        "is_reconciled",
    )
    list_filter = ("transaction_type", "is_reconciled", "date")
    search_fields = ("description", "user__email", "account__name")
    readonly_fields = ("content_hash", "reconciled_at")


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("category", "user", "amount", "period_start", "period_end")
    list_filter = ("period_start",)
    search_fields = ("category__name", "user__email")


@admin.register(Debt)
class DebtAdmin(admin.ModelAdmin):
    list_display = ("nombre", "user", "prestamista", "monto_requerido", "monto_pagado", "fecha_limite")
    list_filter = ("fecha_limite",)
    search_fields = ("nombre", "prestamista", "user__email")


@admin.register(Goal)
class GoalAdmin(admin.ModelAdmin):
    list_display = ("nombre", "user", "monto_requerido", "monto_abonado", "fecha_limite")
    list_filter = ("fecha_limite",)
    search_fields = ("nombre", "user__email")


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("user", "contact", "status", "created_at")
    list_filter = ("status",)
    search_fields = ("user__email", "contact__email")


class ContactGroupMembershipInline(admin.TabularInline):
    model = ContactGroupMembership
    extra = 0


@admin.register(ContactGroup)
class ContactGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "created_at")
    search_fields = ("name", "user__email")
    inlines = [ContactGroupMembershipInline]


class SharedExpensePaymentInline(admin.TabularInline):
    model = SharedExpensePayment
    extra = 0


@admin.register(SharedExpenseParticipant)
class SharedExpenseParticipantAdmin(admin.ModelAdmin):
    """Registrado como standalone (además de anidado en `SharedExpenseAdmin`)
    para poder anidar a su vez `SharedExpensePaymentInline`: Django Admin no
    soporta inlines anidados a más de un nivel."""

    list_display = ("shared_expense", "display_name", "is_owner", "is_payer", "amount_assigned", "amount_paid")
    list_filter = ("is_owner", "is_payer")
    inlines = [SharedExpensePaymentInline]


class SharedExpenseParticipantInline(admin.TabularInline):
    model = SharedExpenseParticipant
    extra = 0


@admin.register(SharedExpense)
class SharedExpenseAdmin(admin.ModelAdmin):
    list_display = ("name", "user", "split_method", "created_at")
    list_filter = ("split_method",)
    search_fields = ("name", "user__email")
    inlines = [SharedExpenseParticipantInline]


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "transaction", "content_type", "size", "uploaded_at")
    readonly_fields = ("size", "content_type", "original_filename")
