"""Importación y exportación de transacciones en formato CSV.

La importación detecta duplicados por `content_hash` (mismo usuario, cuenta,
fecha, monto y descripción) para poder reimportar el mismo archivo sin crear
transacciones repetidas.
"""

import csv
import io
from decimal import Decimal, InvalidOperation
from datetime import datetime

from django.db import transaction as db_transaction

from core.models import Account, Category, Transaction


CSV_HEADERS = [
    "date",
    "account",
    "category",
    "transaction_type",
    "amount",
    "description",
    "notes",
]


def export_transactions_csv(user, queryset=None):
    """Exporta las transacciones (del usuario, o del queryset dado) a un string CSV."""
    if queryset is None:
        queryset = Transaction.objects.filter(user=user).select_related(
            "account", "category"
        )

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_HEADERS)
    writer.writeheader()

    for tx in queryset:
        writer.writerow(
            {
                "date": tx.date.isoformat(),
                "account": tx.account.name,
                "category": tx.category.name if tx.category else "",
                "transaction_type": tx.transaction_type,
                "amount": str(tx.amount),
                "description": tx.description,
                "notes": tx.notes,
            }
        )

    return output.getvalue()


def import_transactions_csv(user, csv_content):
    """
    Import transactions from CSV with duplicate detection by content_hash.
    Returns dict with counts: created, skipped_duplicates, errors.
    """
    reader = csv.DictReader(io.StringIO(csv_content))
    created = 0
    skipped_duplicates = 0
    errors = []

    existing_hashes = set(
        Transaction.objects.filter(user=user).values_list("content_hash", flat=True)
    )

    account_cache = {a.name: a for a in Account.objects.filter(user=user)}
    category_cache = {
        (c.name, c.category_type): c
        for c in Category.objects.filter(user=user)
    }

    for row_num, row in enumerate(reader, start=2):
        try:
            account_name = row.get("account", "").strip()
            account = account_cache.get(account_name)
            if not account:
                errors.append(f"Fila {row_num}: cuenta '{account_name}' no encontrada")
                continue

            date_str = row.get("date", "").strip()
            try:
                tx_date = datetime.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                errors.append(f"Fila {row_num}: fecha inválida '{date_str}'")
                continue

            amount = Decimal(row.get("amount", "0").strip())
            description = row.get("description", "").strip()
            if not description:
                errors.append(f"Fila {row_num}: descripción vacía")
                continue

            tx_type = row.get("transaction_type", "expense").strip()
            if tx_type not in ("income", "expense", "transfer"):
                errors.append(f"Fila {row_num}: tipo inválido '{tx_type}'")
                continue

            content_hash = Transaction.compute_hash(
                user.pk, account.pk, tx_date, amount, description
            )
            if content_hash in existing_hashes:
                skipped_duplicates += 1
                continue

            category = None
            cat_name = row.get("category", "").strip()
            if cat_name:
                category = category_cache.get((cat_name, tx_type))
                if not category and tx_type != "transfer":
                    category = category_cache.get((cat_name, "expense")) or category_cache.get(
                        (cat_name, "income")
                    )

            with db_transaction.atomic():
                Transaction.objects.create(
                    user=user,
                    account=account,
                    category=category,
                    transaction_type=tx_type,
                    amount=amount,
                    description=description,
                    date=tx_date,
                    notes=row.get("notes", "").strip(),
                )
            existing_hashes.add(content_hash)
            created += 1

        except (InvalidOperation, KeyError, ValueError) as exc:
            errors.append(f"Fila {row_num}: {exc}")

    return {
        "created": created,
        "skipped_duplicates": skipped_duplicates,
        "errors": errors,
    }
