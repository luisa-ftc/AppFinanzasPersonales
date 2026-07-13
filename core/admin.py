"""Configuración del admin de Django para los modelos de FinTrack."""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

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


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "transaction", "content_type", "size", "uploaded_at")
    readonly_fields = ("size", "content_type", "original_filename")
