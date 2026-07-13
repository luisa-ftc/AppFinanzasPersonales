"""Mapa de rutas de la API REST de FinTrack: router DRF con los ViewSets
CRUD, autenticación (registro, perfil, JWT si `JWT_ENABLED`), dashboard y
reporte PDF."""

from django.conf import settings
from django.urls import include, path
from rest_framework.routers import DefaultRouter

from core.api import views
from core.api.auth_views import EmailTokenObtainPairView
from rest_framework_simplejwt.views import TokenRefreshView

router = DefaultRouter()
router.register("accounts", views.AccountViewSet, basename="account")
router.register("categories", views.CategoryViewSet, basename="category")
router.register("tags", views.TagViewSet, basename="tag")
router.register("budgets", views.BudgetViewSet, basename="budget")
router.register("debts", views.DebtViewSet, basename="debt")
router.register("goals", views.GoalViewSet, basename="goal")
router.register("contacts", views.ContactViewSet, basename="contact")
router.register("contact-groups", views.ContactGroupViewSet, basename="contact-group")
router.register("transactions", views.TransactionViewSet, basename="transaction")

urlpatterns = [
    path("", include(router.urls)),
    path("auth/register/", views.RegisterAPIView.as_view(), name="api-register"),
    path("auth/me/", views.MeAPIView.as_view(), name="api-me"),
    path("dashboard/", views.DashboardAPIView.as_view(), name="api-dashboard"),
    path("reports/pdf/", views.PDFReportAPIView.as_view(), name="api-pdf-report"),
]

if settings.JWT_ENABLED:
    urlpatterns += [
        path("auth/token/", EmailTokenObtainPairView.as_view(), name="token_obtain_pair"),
        path("auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    ]
