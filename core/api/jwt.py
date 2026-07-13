"""Serializer JWT adaptado al modelo de usuario de FinTrack, que se autentica por email."""

from rest_framework_simplejwt.serializers import TokenObtainPairSerializer


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Sobreescribe `username_field` a "email" porque `User.USERNAME_FIELD` es el correo, no el username."""

    username_field = "email"
