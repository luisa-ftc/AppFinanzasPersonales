"""Punto de entrada ASGI para servir FinTrack de forma asíncrona."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fintrack.settings")

application = get_asgi_application()
