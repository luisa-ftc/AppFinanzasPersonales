# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Descripción del proyecto

FinTrack es una app de gestión financiera personal construida con Django 4.x + Django REST Framework. Expone el mismo modelo de dominio a través de dos interfaces paralelas: una interfaz web clásica renderizada por el servidor (vistas basadas en clases + templates de Django) y una API REST (ViewSets de DRF + JWT opcional), ambas apoyadas en una misma capa de servicios. Todo el código de la app vive en una única app de Django, `core`; `fintrack/` es solo la configuración a nivel de proyecto (settings/urls/wsgi/asgi).

## Comandos

El entorno es un `.venv` local (Python 3.11+). En Windows, actívalo con `.\.venv\Scripts\Activate.ps1`; en Linux/macOS, `source .venv/bin/activate`.

```bash
# Configuración inicial
pip install -r requirements.txt
cp .env.example .env          # en Windows: copy .env.example .env
python manage.py migrate
python manage.py setup_demo     # datos demo idempotentes: demo@fintrack.local / demo1234
python manage.py createsuperuser
python manage.py runserver

# Tests
pytest                          # suite completa
pytest core/tests/test_core.py::TestAccountBalance            # una clase de test
pytest core/tests/test_core.py::TestAccountBalance::test_balance_with_transactions  # un test
pytest -k csv                   # por palabra clave

# Lint / formateo (configuración en pyproject.toml y .flake8)
black .
isort .
flake8 .
pre-commit run --all-files

# Verificación de Django (sin levantar el servidor)
python manage.py check
```

Configuración de tests: `pytest.ini_options` en `pyproject.toml` define `DJANGO_SETTINGS_MODULE=fintrack.settings` y ejecuta con `-v --tb=short`, así que `pytest` desde la raíz del repo funciona directamente. Los tests usan fixtures de `pytest-django` (`db`, `client`) más fixtures/factories locales, no `TestCase` de Django.

## Arquitectura

### Capas: las vistas/API son delgadas, `core/services/` concentra la lógica

La lógica de negocio —cálculo de saldo de cuentas, cálculo de gasto de presupuestos, import/export CSV, generación de reportes/PDF— vive en `core/services/*.py` (`accounts.py`, `budgets.py`, `csv_io.py`, `reports.py`), no en las vistas ni en los serializers. Tanto `core/views.py` (web) como `core/api/views.py` (DRF) llaman a las mismas funciones de servicio, pero cada una arma su propia forma de respuesta de manera independiente (ej. `DashboardView.get_context_data` y `DashboardAPIView.get` llaman ambas a `get_user_total_balance`/`get_monthly_income_expense`/`get_category_distribution`, pero ensamblan el payload por separado). Al cambiar lógica de saldos/presupuestos/reportes/CSV, edita la función de servicio una sola vez —ambas superficies la recogen—, pero espera tener que tocar ambos archivos de vistas si cambia la *forma* de la respuesta.

### El aislamiento de datos por usuario se aplica en tres lugares, no de forma centralizada

No existe un manager/queryset personalizado que acote automáticamente por usuario. El aislamiento se repite:
- Vistas web: `UserOwnedMixin` (`core/views.py`) filtra `get_queryset()` por `request.user`.
- API: `UserOwnedViewSet` (`core/api/views.py`) hace lo mismo para los ViewSets, y además `perform_create` asigna el `user`.
- Serializers: varios métodos `__init__` reacotan los querysets relacionados (cuenta/categoría/etiquetas) al usuario de la petición (ej. `TransactionSerializer.__init__`, `BudgetSerializer.__init__`) para que un usuario no pueda asociar la cuenta/categoría de otro usuario vía la API.
- Los servicios que tocan datos de usuarios reciben siempre un argumento `user` explícito (`core/services/csv_io.py`, `core/services/reports.py`) en vez de depender de un acotamiento ambiental.

Al agregar un nuevo modelo/endpoint que pertenezca a un usuario, hay que replicar el tratamiento en los cuatro puntos anteriores — no hay un atajo centralizado.

### Los saldos y el avance de presupuesto siempre se derivan, nunca se almacenan

`Account.balance`, `Budget.spent`, `Budget.remaining` y `Budget.percent_used` son métodos `@property` que recalculan a partir de las filas de `Transaction` en cada acceso (vía `core/services/accounts.py` y `core/services/budgets.py`). No existe una columna de saldo cacheada/desnormalizada. `AccountSerializer.get_balance` y `BudgetSerializer.to_representation` hacen este recálculo explícitamente para la API. No agregues caché sin también actualizar cada punto de escritura que necesitaría invalidarla (crear/editar/eliminar transacciones, transferencias).

### Detección de transacciones duplicadas vía hash de contenido

`Transaction.content_hash` (SHA-256 de `user|account|date|amount|description.strip().lower()`, ver `Transaction.compute_hash`) se recalcula en cada `save()`. `core/services/csv_io.py:import_transactions_csv` usa este hash para omitir filas ya importadas, lo cual hace idempotentes tanto el cargador de datos demo (`core/management/commands/setup_demo.py`) como las reimportaciones de CSV. Si cambias qué campos identifican una transacción como "duplicada", actualiza `compute_hash` y ten en cuenta que eso cambia los hashes de todas las filas existentes (no hay ninguna migración que los recalcule).

### Modelo de autenticación: email como username

`core.User` (`AUTH_USER_MODEL = "core.User"`) define `USERNAME_FIELD = "email"`. Esto tiene efectos en cadena que deben mantenerse sincronizados al tocarlos:
- `core/forms.py: LoginForm` sobreescribe `username` para que sea un `EmailField`.
- `core/api/jwt.py: EmailTokenObtainPairSerializer` sobreescribe `username_field = "email"` para el login vía JWT.
- `core/admin.py: UserAdmin` reordena los `fieldsets` de `BaseUserAdmin` para no mostrar el email dos veces.

### El JWT es opcional y depende de un setting

`JWT_ENABLED` (desde `.env`, por defecto `True`) controla si `rest_framework_simplejwt.authentication.JWTAuthentication` se inserta en `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]` y si las rutas `/api/auth/token/` y `/api/auth/token/refresh/` se registran en `core/api/urls.py`. La autenticación por sesión (`SessionAuthentication`) siempre está disponible, sin importar este flag.

### Estructura de rutas

- `fintrack/urls.py` monta: `/admin/`, `core.urls` en `/` (interfaz web), `core.api.urls` en `/api/` (REST), además del schema/Swagger/Redoc de drf-spectacular bajo `/api/schema/`, `/api/docs/`, `/api/redoc/`.
- `core/api/urls.py` usa un `DefaultRouter` de DRF para los cinco ViewSets (cuentas, categorías, etiquetas, presupuestos, transacciones) más rutas explícitas para registro/perfil/dashboard/reporte-pdf y (condicionalmente) JWT.
- Las `@action` personalizadas en `TransactionViewSet` (`core/api/views.py`) exponen `reconcile`, `unreconcile`, `upload_attachment`, `export_csv`, `import_csv` como subrutas, replicando las vistas web equivalentes en `core/urls.py`.

### Formato de import/export CSV

Columnas esperadas: `date,account,category,transaction_type,amount,description,notes` (ver `CSV_HEADERS` en `core/services/csv_io.py`). Cuenta/categoría se emparejan por nombre dentro de los propios registros del usuario que importa; una cuenta sin coincidencia hace que la fila falle (nunca se crea silenciosamente), una categoría sin coincidencia se deja en null.

## Estilo de código

- Se aplica con `black` (line length 88), `isort` (profile `black`, `known_first_party = ["core", "fintrack"]`) y `flake8` (`.flake8`: max-line-length 88, ignora `E203,W503`) — todos conectados en `.pre-commit-config.yaml`.
- Los textos de cara al usuario (labels de formularios, `verbose_name` de modelos, mensajes de éxito) están en español; mantén ese criterio en texto nuevo.
