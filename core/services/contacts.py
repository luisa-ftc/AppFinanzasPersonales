"""Servicios de lógica de negocio para el módulo de contactos.

La relación de contacto es bidireccional y se persiste como filas espejo
(ver `core.models.Contact`). Estas funciones son el único punto de
creación/eliminación de contactos: mantienen ambas direcciones sincronizadas
dentro de una transacción atómica.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from core.models import Contact

User = get_user_model()


def add_contact(user, contact_user):
    """Crea la relación de contacto bidireccional entre dos usuarios.

    Crea las dos filas espejo (user→contact y contact→user) en una
    transacción atómica. Es idempotente: si la relación ya existe (en
    cualquiera de las dos direcciones) no se duplica, gracias a
    `get_or_create` + `unique_together (user, contact)`.

    Retorna la fila del usuario que agrega (`user` → `contact_user`).
    """
    if user.pk == contact_user.pk:
        raise ValidationError("No puedes agregarte a ti mismo como contacto.")

    with db_transaction.atomic():
        own_row, _ = Contact.objects.get_or_create(user=user, contact=contact_user)
        Contact.objects.get_or_create(user=contact_user, contact=user)
    return own_row


def remove_contact(user, contact_user):
    """Elimina la relación de contacto en ambas direcciones.

    No elimina ningún usuario del sistema: solo las dos filas espejo de la
    relación, para que ninguno de los dos siga viendo al otro en su lista.
    """
    with db_transaction.atomic():
        Contact.objects.filter(user=user, contact=contact_user).delete()
        Contact.objects.filter(user=contact_user, contact=user).delete()


def search_users(user, query, limit=10):
    """Busca usuarios registrados por correo para agregarlos como contacto.

    Excluye al propio usuario y a quienes ya son sus contactos. Con menos de
    2 caracteres retorna vacío para no listar toda la base de usuarios.
    """
    query = (query or "").strip()
    if len(query) < 2:
        return User.objects.none()

    existing_ids = Contact.objects.filter(user=user).values_list(
        "contact_id", flat=True
    )
    return (
        User.objects.filter(email__icontains=query)
        .exclude(pk=user.pk)
        .exclude(pk__in=existing_ids)
        .order_by("email")[:limit]
    )
