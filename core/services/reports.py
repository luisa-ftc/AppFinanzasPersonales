"""Generación de reportes (PDF) y datos agregados para los gráficos del dashboard."""

import io
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import TruncMonth
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core.models import Transaction


def generate_transactions_pdf(user, queryset=None):
    """Genera un PDF tabular con las transacciones del usuario (máx. 100 si no se pasa queryset)."""
    if queryset is None:
        queryset = Transaction.objects.filter(user=user).select_related(
            "account", "category"
        )[:100]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph(f"Reporte FinTrack - {user.email}", styles["Title"]))
    elements.append(Spacer(1, 12))

    data = [["Fecha", "Cuenta", "Categoría", "Tipo", "Monto", "Descripción"]]
    for tx in queryset:
        data.append(
            [
                tx.date.isoformat(),
                tx.account.name,
                tx.category.name if tx.category else "-",
                tx.get_transaction_type_display(),
                f"${tx.amount:,.2f}",
                tx.description[:40],
            ]
        )

    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#6366f1")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
            ]
        )
    )
    elements.append(table)

    doc.build(elements)
    buffer.seek(0)
    return buffer


def get_monthly_income_expense(user, months=6):
    """Devuelve ingresos y gastos agrupados por mes (últimos `months` meses) para el gráfico de barras."""
    from datetime import date

    today = date.today()
    month_starts = []
    current = today.replace(day=1)
    for _ in range(months):
        month_starts.insert(0, current)
        if current.month == 1:
            current = current.replace(year=current.year - 1, month=12)
        else:
            current = current.replace(month=current.month - 1)

    start = month_starts[0]

    qs = (
        Transaction.objects.filter(user=user, date__gte=start)
        .annotate(month=TruncMonth("date"))
        .values("month", "transaction_type")
        .annotate(total=Sum("amount"))
        .order_by("month")
    )

    rows_by_month = {}
    for row in qs:
        month_value = row["month"]
        if isinstance(month_value, datetime):
            key = month_value.date()
        elif isinstance(month_value, date):
            key = month_value
        else:
            key = month_value

        if key not in rows_by_month:
            rows_by_month[key] = {"income": Decimal("0"), "expense": Decimal("0")}
        if row["transaction_type"] == "income":
            rows_by_month[key]["income"] = row["total"]
        elif row["transaction_type"] == "expense":
            rows_by_month[key]["expense"] = row["total"]

    month_labels = []
    income_data = []
    expense_data = []

    for month_start in month_starts:
        month_labels.append(month_start.strftime("%b %Y"))
        data = rows_by_month.get(month_start, {"income": Decimal("0"), "expense": Decimal("0")})
        income_data.append(float(data["income"]))
        expense_data.append(float(data["expense"]))

    return {
        "labels": month_labels,
        "income": income_data,
        "expense": expense_data,
    }


def get_category_distribution(user, tx_type="expense"):
    """Devuelve el top 10 de categorías por monto total del tipo dado, para el gráfico de pastel."""
    qs = (
        Transaction.objects.filter(user=user, transaction_type=tx_type, category__isnull=False)
        .values("category__name", "category__color")
        .annotate(total=Sum("amount"))
        .order_by("-total")[:10]
    )

    return {
        "labels": [r["category__name"] for r in qs],
        "data": [float(r["total"]) for r in qs],
        "colors": [r["category__color"] for r in qs],
    }
