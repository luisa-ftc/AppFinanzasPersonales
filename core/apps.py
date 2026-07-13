"""Configuración de la app Django `core`, única app de dominio del proyecto."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Configuración estándar de la app `core` (modelos, vistas, API y servicios de FinTrack)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "FinTrack Core"
