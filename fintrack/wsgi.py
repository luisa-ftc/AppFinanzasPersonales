"""Punto de entrada WSGI para servir FinTrack en producción (síncrono)."""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fintrack.settings")

application = get_wsgi_application()
