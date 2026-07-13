"""Vistas de la API REST de FinTrack (DRF): ViewSets CRUD para cuentas,
categorías, etiquetas, presupuestos y transacciones, más endpoints de
autenticación, dashboard y reporte PDF. Toda la lógica de negocio se delega
en `core.services`; el aislamiento por usuario se centraliza en
`UserOwnedViewSet`.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.api.serializers import (
    AccountSerializer,
    BudgetSerializer,
    CategorySerializer,
    RegisterSerializer,
    TagSerializer,
    TransactionSerializer,
    UserSerializer,
)
from core.models import Account, Attachment, Budget, Category, Tag, Transaction
from core.services.accounts import calculate_account_balance, get_user_total_balance
from core.services.csv_io import export_transactions_csv, import_transactions_csv
from core.services.reports import (
    generate_transactions_pdf,
    get_category_distribution,
    get_monthly_income_expense,
)

User = get_user_model()


class RegisterAPIView(generics.CreateAPIView):
    """Registro de usuarios vía API, abierto a peticiones no autenticadas."""

    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class MeAPIView(generics.RetrieveAPIView):
    """Devuelve los datos del usuario autenticado."""

    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user


class UserOwnedViewSet(viewsets.ModelViewSet):
    """ViewSet base que exige autenticación y restringe queryset y creación al
    usuario de la petición; equivalente en la API a `UserOwnedMixin` en las
    vistas web."""

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return self.queryset.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class AccountViewSet(UserOwnedViewSet):
    """CRUD de cuentas del usuario autenticado."""

    queryset = Account.objects.all()
    serializer_class = AccountSerializer


class CategoryViewSet(UserOwnedViewSet):
    """CRUD de categorías del usuario autenticado."""

    queryset = Category.objects.all()
    serializer_class = CategorySerializer


class TagViewSet(UserOwnedViewSet):
    """CRUD de etiquetas del usuario autenticado."""

    queryset = Tag.objects.all()
    serializer_class = TagSerializer


class BudgetViewSet(UserOwnedViewSet):
    """CRUD de presupuestos del usuario autenticado."""

    queryset = Budget.objects.select_related("category")
    serializer_class = BudgetSerializer


class TransactionViewSet(UserOwnedViewSet):
    """CRUD de transacciones del usuario autenticado, con acciones adicionales
    de conciliación, adjuntos e import/export CSV."""

    queryset = Transaction.objects.select_related("account", "category").prefetch_related(
        "tags", "attachments"
    )
    serializer_class = TransactionSerializer
    filterset_fields = [
        "account",
        "category",
        "transaction_type",
        "is_reconciled",
        "date",
    ]
    search_fields = ["description", "notes"]
    ordering_fields = ["date", "amount", "created_at"]

    @action(detail=True, methods=["post"])
    def reconcile(self, request, pk=None):
        """Marca la transacción como conciliada."""
        tx = self.get_object()
        tx.reconcile()
        return Response(TransactionSerializer(tx, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def unreconcile(self, request, pk=None):
        """Revierte la conciliación de la transacción."""
        tx = self.get_object()
        tx.unreconcile()
        return Response(TransactionSerializer(tx, context={"request": request}).data)

    @action(detail=True, methods=["post"])
    def upload_attachment(self, request, pk=None):
        """Sube un adjunto a la transacción, validando tamaño y tipo antes de guardarlo."""
        tx = self.get_object()
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response(
                {"detail": "Archivo requerido."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        attachment = Attachment(
            transaction=tx,
            file=uploaded,
            original_filename=uploaded.name,
            content_type=uploaded.content_type,
            size=uploaded.size,
        )
        try:
            attachment.full_clean()
            attachment.save()
        except Exception as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"id": attachment.id, "filename": attachment.original_filename})

    @action(detail=False, methods=["get"])
    def export_csv(self, request):
        """Exporta a CSV las transacciones del usuario, respetando los filtros aplicados en la petición."""
        content = export_transactions_csv(request.user, self.filter_queryset(self.get_queryset()))
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="transacciones.csv"'
        return response

    @action(detail=False, methods=["post"])
    def import_csv(self, request):
        """Importa transacciones desde un archivo CSV subido, con detección de duplicados."""
        uploaded = request.FILES.get("file")
        if not uploaded:
            return Response(
                {"detail": "Archivo CSV requerido."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        content = uploaded.read().decode("utf-8-sig")
        result = import_transactions_csv(request.user, content)
        return Response(result)


class DashboardAPIView(APIView):
    """Datos agregados del dashboard: saldo total, saldo por cuenta y gráficos mensual/por categoría."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        accounts = Account.objects.filter(user=user, is_active=True)
        return Response(
            {
                "total_balance": str(get_user_total_balance(user)),
                "accounts": [
                    {
                        **AccountSerializer(a, context={"request": request}).data,
                        "balance": str(calculate_account_balance(a)),
                    }
                    for a in accounts
                ],
                "monthly_chart": get_monthly_income_expense(user),
                "category_chart": get_category_distribution(user),
            }
        )


class PDFReportAPIView(APIView):
    """Descarga en PDF de un reporte de transacciones del usuario autenticado."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        buffer = generate_transactions_pdf(request.user)
        response = HttpResponse(buffer.read(), content_type="application/pdf")
        response["Content-Disposition"] = 'attachment; filename="reporte.pdf"'
        return response
