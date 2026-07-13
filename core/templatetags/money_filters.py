"""Filtros de template para formatear valores monetarios en las plantillas de FinTrack."""

from django import template

register = template.Library()


@register.filter
def format_money(value):
    """Formatea un valor como texto monetario con separador de miles y 2 decimales."""
    try:
        if value is None:
            return "0.00"
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"
