"""Servicios de lógica de negocio para el módulo de gastos compartidos.

Un gasto compartido solo genera una `Transaction` de gasto **al crearse**
cuando el dueño de la app fue quien realmente pagó (`is_owner=True` en el
participante pagador): solo ahí salió dinero real de una de sus cuentas. Si
pagó un contacto, no se crea ninguna transacción en ese momento — el gasto
queda pendiente de que el dueño salde su propia parte más adelante.

Registrar un pago (`register_shared_expense_payment`) genera una
`Transaction` real solo cuando el dueño participa directamente en ese
movimiento concreto (ver `get_shared_expense_payment_transaction_type`):
- Si el dueño está saldando su propia parte (pagó un contacto originalmente)
  → `Transaction` de tipo **gasto**: es el momento en que sale dinero real de
  una de sus cuentas, igual que cualquier otro gasto personal.
- Si el dueño está recibiendo el pago de otro participante (el dueño pagó el
  gasto originalmente) → `Transaction` de tipo **ingreso**.
- Si el movimiento es entre dos terceros (un contacto le paga a otro
  contacto que pagó el gasto original) → ninguna transacción: no toca
  ninguna cuenta del dueño, sigue siendo puramente informativo.

Deliberadamente **no** se modela como una `Debt`: el saldo que el dueño le
debe a un contacto ya lo administra `SharedExpenseParticipant.amount_pending`
de su propia fila; crear además una `Debt` duplicaría la información.
"""

from collections import namedtuple
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction as db_transaction

from core.models import (
    SharedExpense,
    SharedExpenseParticipant,
    SharedExpensePayment,
    Transaction,
)

ParticipantSpec = namedtuple("ParticipantSpec", ["is_owner", "contact"])


def calculate_equal_split(total_amount, participant_count):
    """Reparte `total_amount` en `participant_count` partes iguales, en
    centavos exactos (`Decimal`, sin floats). El remanente de centavos que
    deja la división entera se distribuye de a uno entre las primeras
    posiciones de la lista resultante (posición 0 = primer participante).

    No depende de `SplitMethod`: es la única función que un método de
    reparto futuro (porcentajes, montos personalizados) tendría que
    reemplazar, sin tocar `SharedExpense`/`SharedExpenseParticipant`.
    """
    if participant_count <= 0:
        raise ValueError("participant_count debe ser mayor a 0.")
    if total_amount <= 0:
        raise ValueError("total_amount debe ser mayor a 0.")

    cents_total = int((total_amount * 100).to_integral_value(rounding=ROUND_HALF_UP))
    base_cents, remainder_cents = divmod(cents_total, participant_count)
    amounts_cents = [base_cents] * participant_count
    for i in range(remainder_cents):
        amounts_cents[i] += 1
    return [(Decimal(c) / 100).quantize(Decimal("0.01")) for c in amounts_cents]


SPLIT_CALCULATORS = {
    SharedExpense.SplitMethod.EQUAL: calculate_equal_split,
}


def resolve_participants(user, contact_ids, group_ids):
    """Resuelve la lista deduplicada de `Contact` a incluir como participantes.

    Orden: primero los contactos individuales (en el orden de `contact_ids`),
    luego los integrantes de cada grupo de `group_ids` (en el orden de los
    grupos, y dentro de cada grupo por `ContactGroupMembership.id` ascendente
    para determinismo), omitiendo cualquier contacto ya visto.
    """
    from core.models import Contact, ContactGroup

    seen_ids = set()
    result = []

    contacts_by_id = {
        c.pk: c for c in Contact.objects.filter(user=user, pk__in=contact_ids)
    }
    for contact_id in contact_ids:
        contact = contacts_by_id.get(contact_id)
        if contact and contact.pk not in seen_ids:
            seen_ids.add(contact.pk)
            result.append(contact)

    groups = ContactGroup.objects.filter(user=user, pk__in=group_ids).prefetch_related(
        "memberships__contact"
    )
    groups_by_id = {g.pk: g for g in groups}
    for group_id in group_ids:
        group = groups_by_id.get(group_id)
        if group is None:
            continue
        for membership in group.memberships.order_by("id"):
            contact = membership.contact
            if contact.pk not in seen_ids:
                seen_ids.add(contact.pk)
                result.append(contact)

    return result


def build_participant_specs(include_owner, contacts):
    """Compone la lista final de specs: el dueño (si `include_owner`) primero,
    luego un spec por cada contacto resuelto, preservando su orden."""
    specs = []
    if include_owner:
        specs.append(ParticipantSpec(True, None))
    specs.extend(ParticipantSpec(False, contact) for contact in contacts)
    return specs


def _specs_match(a, b):
    """Compara dos `ParticipantSpec`: mismo "slot" si ambos son el dueño, o
    si ambos son el mismo contacto."""
    if a.is_owner or b.is_owner:
        return a.is_owner and b.is_owner
    return bool(a.contact and b.contact and a.contact.pk == b.contact.pk)


def validate_payer_is_participant(payer_spec, specs):
    """Lanza ValidationError si `payer_spec` no aparece entre los participantes."""
    if not any(_specs_match(payer_spec, spec) for spec in specs):
        raise ValidationError(
            "El pagador seleccionado no está entre los participantes elegidos."
        )


def create_shared_expense(
    *,
    user,
    name,
    description,
    account,
    category,
    date,
    total_amount,
    participant_specs,
    payer_spec,
    split_method=SharedExpense.SplitMethod.EQUAL,
):
    """Crea el gasto compartido completo: el SharedExpense, sus participantes
    con el reparto ya calculado y, **solo si el dueño es quien pagó**, la
    Transacción de gasto asociada. Lanza ValidationError si no hay
    participantes, si el pagador no pertenece a ellos, o si paga el dueño y
    no se indicó cuenta de origen (sin cuenta no hay cómo descontar dinero
    real). Si paga un contacto, `account` puede venir `None`: el gasto queda
    sin `Transaction`, es puramente informativo.
    """
    if not participant_specs:
        raise ValidationError("Debes seleccionar al menos un participante.")
    validate_payer_is_participant(payer_spec, participant_specs)
    if payer_spec.is_owner and account is None:
        raise ValidationError(
            "Selecciona la cuenta de origen: tú pagaste este gasto."
        )

    calculator = SPLIT_CALCULATORS[split_method]
    amounts = calculator(total_amount, len(participant_specs))

    with db_transaction.atomic():
        transaction = None
        if payer_spec.is_owner:
            transaction = Transaction.objects.create(
                user=user,
                account=account,
                category=category,
                transaction_type=Transaction.TransactionType.EXPENSE,
                amount=total_amount,
                description=name,
                date=date,
            )
        shared_expense = SharedExpense.objects.create(
            user=user,
            name=name,
            description=description,
            category=category,
            date=date,
            total_amount=total_amount,
            split_method=split_method,
            account=account if payer_spec.is_owner else None,
            transaction=transaction,
        )

        pairs = enumerate(zip(participant_specs, amounts))
        for position, (spec, amount_assigned) in pairs:
            is_payer = _specs_match(spec, payer_spec)
            SharedExpenseParticipant.objects.create(
                shared_expense=shared_expense,
                contact=spec.contact,
                is_owner=spec.is_owner,
                is_payer=is_payer,
                position=position,
                amount_assigned=amount_assigned,
                amount_paid=amount_assigned if is_payer else Decimal("0.00"),
            )

    return shared_expense


def validate_payment_against_participant(participant, amount):
    """Lanza ValidationError si el pago supera el saldo pendiente del participante."""
    if amount > participant.amount_pending:
        raise ValidationError(
            f"El monto recibido (${amount:,.2f}) supera el saldo pendiente "
            f"del participante (${participant.amount_pending:,.2f})."
        )


def get_shared_expense_payment_transaction_type(participant, payer):
    """Determina qué tipo de `Transaction` genera un pago de gasto compartido,
    según quién paga y quién recibe en ese movimiento concreto:

    - `participant.is_owner` → el dueño es quien está saldando su propia
      parte ahora mismo: sale dinero real de una de sus cuentas → `EXPENSE`.
    - `payer.is_owner` (y el participante no es el dueño) → el dueño es quien
      recibe: entra dinero real a una de sus cuentas → `INCOME`.
    - Ninguno de los dos es el dueño (un contacto le paga a otro contacto que
      pagó el gasto original) → `None`: movimiento puramente entre terceros,
      no toca ninguna cuenta del dueño, sigue siendo informativo.

    Ambos casos no pueden darse a la vez: si el dueño pagó el gasto original,
    su propia fila queda auto-saldada desde la creación (nunca tiene saldo
    pendiente que registrar como pago).
    """
    if participant.is_owner:
        return Transaction.TransactionType.EXPENSE
    if payer and payer.is_owner:
        return Transaction.TransactionType.INCOME
    return None


def register_shared_expense_payment(
    *, participant, amount, date, notes="", account=None
):
    """Registra un pago de `participant`, acumulando su monto pagado.

    Genera una `Transaction` real (`get_shared_expense_payment_transaction_type`
    decide si es gasto o ingreso) solo cuando el dueño de la app participa
    directamente en este movimiento concreto —pagando su propia parte o
    recibiendo el pago de otro—; en ese caso `account` es obligatoria. Si el
    movimiento es puramente entre terceros (un contacto paga a otro), no se
    crea ninguna transacción: el registro sigue siendo informativo.
    """
    validate_payment_against_participant(participant, amount)
    shared_expense = participant.shared_expense
    payer = shared_expense.payer_participant
    transaction_type = get_shared_expense_payment_transaction_type(participant, payer)

    if transaction_type is not None and account is None:
        if transaction_type == Transaction.TransactionType.EXPENSE:
            raise ValidationError("Selecciona la cuenta desde la que pagaste tu parte.")
        raise ValidationError("Selecciona la cuenta donde recibiste el pago.")

    with db_transaction.atomic():
        transaction = None
        if transaction_type == Transaction.TransactionType.EXPENSE:
            description = (
                f"Pago de gasto compartido: {shared_expense.name} "
                f"(a {payer.display_name})"
            )
            transaction = Transaction.objects.create(
                user=shared_expense.user,
                account=account,
                category=shared_expense.category,
                transaction_type=Transaction.TransactionType.EXPENSE,
                amount=amount,
                description=description,
                date=date,
            )
        elif transaction_type == Transaction.TransactionType.INCOME:
            transaction = Transaction.objects.create(
                user=shared_expense.user,
                account=account,
                category=shared_expense.category,
                transaction_type=Transaction.TransactionType.INCOME,
                amount=amount,
                description=(
                    f"Pago recibido: {shared_expense.name} "
                    f"({participant.display_name})"
                ),
                date=date,
            )
        payment = SharedExpensePayment.objects.create(
            participant=participant,
            amount=amount,
            date=date,
            notes=notes,
            account=account if transaction_type is not None else None,
            transaction=transaction,
        )
        participant.amount_paid += amount
        participant.save(update_fields=["amount_paid", "updated_at"])
    return payment


def revert_shared_expense_payment_transaction(transaction):
    """Revierte el efecto de una transacción de ingreso sobre su pago de
    gasto compartido (para cuando esa transacción se elimina directamente
    desde el módulo de Transacciones). Resta el monto del `amount_paid`
    cacheado del participante, dejando el saldo pendiente correcto.

    No-op si la transacción no está vinculada a ningún `SharedExpensePayment`
    (mismo patrón que `revert_transaction_from_debt`/`revert_transaction_from_goal`)."""
    payment = getattr(transaction, "shared_expense_payment", None)
    if payment is None:
        return
    participant = payment.participant
    participant.amount_paid = max(
        participant.amount_paid - payment.amount, Decimal("0.00")
    )
    participant.save(update_fields=["amount_paid", "updated_at"])


def delete_shared_expense(shared_expense):
    """Elimina el gasto compartido.

    Si tiene una Transacción de gasto asociada (el dueño pagó), se borra esa
    Transacción: el `OneToOneField(on_delete=CASCADE)` de
    `SharedExpense.transaction` hace que Django borre en cascada el
    `SharedExpense` y, a su vez, todos sus `SharedExpenseParticipant`/
    `SharedExpensePayment`. Si no tiene Transacción (pagó un contacto), se
    borra el `SharedExpense` directamente, con el mismo efecto en cascada.

    En ambos casos, las Transacciones de **ingreso** de los pagos ya
    recibidos (`SharedExpensePayment.transaction`) no se tocan: ese dinero
    entró de verdad a una cuenta y debe seguir reflejado en Transacciones
    aunque se borre el gasto compartido que le dio origen."""
    if shared_expense.transaction_id:
        shared_expense.transaction.delete()
    else:
        shared_expense.delete()
