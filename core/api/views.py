"""Vistas de la API REST de FinTrack (DRF): ViewSets CRUD para cuentas,
categorías, etiquetas, presupuestos y transacciones, más endpoints de
autenticación, dashboard y reporte PDF. Toda la lógica de negocio se delega
en `core.services`; el aislamiento por usuario se centraliza en
`UserOwnedViewSet`.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
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
    ContactGroupSerializer,
    ContactSerializer,
    DebtSerializer,
    GoalSerializer,
    RegisterSerializer,
    TagSerializer,
    TransactionSerializer,
    UserSerializer,
)
from core.models import (
    Account,
    Attachment,
    Budget,
    Category,
    Contact,
    ContactGroup,
    Debt,
    Goal,
    Tag,
    Transaction,
)
from core.services.contacts import add_contact, remove_contact, search_users
from core.services.debts import apply_transaction_to_debt, revert_transaction_from_debt
from core.services.goals import apply_transaction_to_goal, revert_transaction_from_goal
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

    queryset = Account.objects.select_related("credit_card_details")
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


class DebtViewSet(UserOwnedViewSet):
    """CRUD de deudas del usuario autenticado, con acción adicional para
    listar las transacciones asociadas a una deuda."""

    queryset = Debt.objects.all()
    serializer_class = DebtSerializer

    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        """Lista las transacciones asociadas a la deuda, más recientes primero."""
        debt = self.get_object()
        txs = debt.transactions.select_related("account", "category").order_by("-date")
        serializer = TransactionSerializer(txs, many=True, context={"request": request})
        return Response(serializer.data)


class GoalViewSet(UserOwnedViewSet):
    """CRUD de metas del usuario autenticado, con acción para listar
    las transacciones asociadas a una meta."""

    queryset = Goal.objects.all()
    serializer_class = GoalSerializer

    @action(detail=True, methods=["get"])
    def transactions(self, request, pk=None):
        """Lista las transacciones asociadas a la meta, más recientes primero."""
        goal = self.get_object()
        txs = goal.transactions.select_related("account", "category").order_by("-date")
        serializer = TransactionSerializer(txs, many=True, context={"request": request})
        return Response(serializer.data)


class ContactViewSet(UserOwnedViewSet):
    """CRUD de contactos del usuario autenticado.

    La creación/eliminación delega en `core.services.contacts` para mantener
    las filas espejo de la relación bidireccional sincronizadas.
    """

    queryset = Contact.objects.select_related("contact")
    serializer_class = ContactSerializer

    def create(self, request, *args, **kwargs):
        """Agrega un contacto (relación bidireccional); errores de negocio → 400."""
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = add_contact(request.user, serializer.validated_data["contact"])
        except ValidationError as exc:
            return Response(
                {"detail": exc.messages[0]}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response(
            self.get_serializer(row).data, status=status.HTTP_201_CREATED
        )

    def perform_destroy(self, instance):
        """Elimina la relación en ambas direcciones (no elimina usuarios)."""
        remove_contact(instance.user, instance.contact)

    @action(detail=False, methods=["get"])
    def search(self, request):
        """Busca usuarios registrados por correo para agregarlos como contacto."""
        users = search_users(request.user, request.GET.get("q", ""))
        return Response(
            {
                "results": [
                    {
                        "id": u.pk,
                        "name": u.get_full_name() or u.username,
                        "email": u.email,
                    }
                    for u in users
                ]
            }
        )


class ContactGroupViewSet(UserOwnedViewSet):
    """CRUD de grupos de contactos del usuario autenticado.

    Los integrantes son filas `Contact` del usuario; el serializer acota el
    queryset y sincroniza el M2M (tabla intermedia `ContactGroupMembership`).
    """

    queryset = ContactGroup.objects.prefetch_related("members__contact")
    serializer_class = ContactGroupSerializer


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

    def perform_create(self, serializer):
        """Guarda la transacción del usuario autenticado y aplica su efecto sobre la deuda/meta asociada, si tiene."""
        tx = serializer.save(user=self.request.user)
        apply_transaction_to_debt(tx)
        apply_transaction_to_goal(tx)

    def perform_update(self, serializer):
        """Revierte el efecto de la versión anterior sobre su deuda/meta antes de guardar y aplicar el nuevo."""
        old = Transaction.objects.select_related("debt", "goal").get(
            pk=serializer.instance.pk
        )
        revert_transaction_from_debt(old)
        revert_transaction_from_goal(old)
        tx = serializer.save()
        apply_transaction_to_debt(tx)
        apply_transaction_to_goal(tx)

    def perform_destroy(self, instance):
        """Revierte el efecto de la transacción sobre su deuda/meta asociada antes de eliminarla."""
        revert_transaction_from_debt(instance)
        revert_transaction_from_goal(instance)
        instance.delete()

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
