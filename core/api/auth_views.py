"""Vista de obtención de tokens JWT usando email en vez de username."""

from rest_framework_simplejwt.views import TokenObtainPairView

from core.api.jwt import EmailTokenObtainPairSerializer


class EmailTokenObtainPairView(TokenObtainPairView):
    """Endpoint de login JWT (`/api/auth/token/`) que autentica por email."""

    serializer_class = EmailTokenObtainPairSerializer
