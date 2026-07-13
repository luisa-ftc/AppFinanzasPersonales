# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Descripción del proyecto

FinTrack es una app de gestión financiera personal construida con Django 4.x + Django REST Framework. Expone el mismo modelo de dominio a través de dos interfaces paralelas: una interfaz web clásica renderizada por el servidor (vistas basadas en clases + templates de Django) y una API REST (ViewSets de DRF + JWT opcional), ambas apoyadas en una misma capa de servicios. Todo el código de la app vive en una única app de Django, `core`; `fintrack/` es solo la configuración a nivel de proyecto (settings/urls/wsgi/asgi).

Módulos de negocio (mismo nivel en la nav web y en el router de la API): Dashboard, Cuentas, Categorías, Presupuestos, **Deudas**, **Metas**, Transacciones, **Contactos**.

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

Al agregar un nuevo modelo/endpoint que pertenezca a un usuario, hay que replicar el tratamiento en los cuatro puntos anteriores — no hay un atajo centralizado. `Debt` (deudas) sigue exactamente este patrón: `UserOwnedMixin` en las 5 vistas web (`DebtListView/CreateView/UpdateView/DeleteView/DetailView`), `UserOwnedViewSet` en `DebtViewSet`, y `TransactionForm`/`TransactionSerializer` reacotan `self.fields["debt"].queryset` al usuario en su `__init__`.

### Los saldos y el avance de presupuesto siempre se derivan, nunca se almacenan

`Account.balance`, `Budget.spent`, `Budget.remaining` y `Budget.percent_used` son métodos `@property` que recalculan a partir de las filas de `Transaction` en cada acceso (vía `core/services/accounts.py` y `core/services/budgets.py`). No existe una columna de saldo cacheada/desnormalizada. `AccountSerializer.get_balance` y `BudgetSerializer.to_representation` hacen este recálculo explícitamente para la API. No agregues caché sin también actualizar cada punto de escritura que necesitaría invalidarla (crear/editar/eliminar transacciones, transferencias).

### Detección de transacciones duplicadas vía hash de contenido

`Transaction.content_hash` (SHA-256 de `user|account|date|amount|description.strip().lower()`, ver `Transaction.compute_hash`) se recalcula en cada `save()`. `core/services/csv_io.py:import_transactions_csv` usa este hash para omitir filas ya importadas, lo cual hace idempotentes tanto el cargador de datos demo (`core/management/commands/setup_demo.py`) como las reimportaciones de CSV. Si cambias qué campos identifican una transacción como "duplicada", actualiza `compute_hash` y ten en cuenta que eso cambia los hashes de todas las filas existentes (no hay ninguna migración que los recalcule).

### Cuentas de tarjeta de crédito: modelo de detalle separado, no campos en `Account`

`Account.account_type == "credit"` puede tener un `AccountCreditCardDetails` asociado (`OneToOneField` a `Account`, con `credit_limit`, `statement_day`, `payment_due_day`). Es el patrón a replicar para futuros tipos de cuenta con campos propios (ej. `AccountInvestmentDetails`): un modelo de detalle aparte, nunca agregando campos condicionales a `Account`. `AccountCreditCardDetails.clean()` valida que solo pueda asociarse a una cuenta de tipo `CREDIT`.

Los cálculos de crédito usado/disponible y próximas fechas de corte/pago viven en `core/services/credit_cards.py` y son **independientes** de `calculate_account_balance` (`core/services/accounts.py`): en una cuenta normal un gasto *resta* saldo, pero en una tarjeta un gasto *aumenta* la deuda y un pago (transferencia entrante) la *reduce* — es la relación de signos invertida. `AccountSerializer` y las vistas de cuentas exponen `used_credit`/`available_credit`/`next_statement_date`/`next_payment_due_date` como campos calculados solo cuando `account_type == CREDIT` y existe el detalle; en cualquier otro caso son `None`.

### Módulo de Deudas: integrado con Transacciones vía llamadas explícitas de servicio, no señales

`Debt` (`core/models.py`) registra un préstamo con `monto_requerido`, `monto_pagado` (`monto_pendiente` y `estado` — pendiente/pagada/vencida — son `@property` derivadas, igual que los saldos de cuentas). `Transaction.debt` es un FK opcional (`on_delete=SET_NULL`, para conservar el historial de transacciones si se borra la deuda).

Cuando una transacción tiene `debt` asignada:
- `income` → suma a `monto_requerido` de la deuda (el usuario recibió más dinero prestado).
- `expense` → suma a `monto_pagado` (el usuario abonó a la deuda); se valida contra `monto_pendiente` antes de guardar (`validate_expense_against_debt`, llamada desde `TransactionForm.clean()` y `TransactionSerializer`) para no permitir sobrepagos.
- `transfer` → no afecta ninguna deuda.

Esta lógica vive en `core/services/debts.py` (`apply_transaction_to_debt`, `revert_transaction_from_debt`, `validate_expense_against_debt`, `get_debt_transaction_history`) y se invoca **explícitamente** desde las vistas/ViewSets de Transaction (`TransactionCreateView/UpdateView/DeleteView` en `core/views.py`, `TransactionViewSet.perform_create/update/destroy` en `core/api/views.py`) envuelta en `db_transaction.atomic()` — nunca desde `Transaction.save()` ni señales, para no acoplar esa lógica al guardado genérico de transacciones (que ya tiene su propio cálculo de `content_hash`). Al editar o eliminar una transacción con deuda asociada, siempre se revierte el efecto de la versión anterior antes de aplicar el nuevo, para que los montos de la deuda no queden desincronizados.

### Módulo de Metas: espejo de Deudas con la relación de signos invertida

`Goal` (`core/models.py`) representa una meta de ahorro/inversión con `monto_requerido` y `monto_abonado` (`monto_pendiente` y `estado` —solo pendiente/completada— son `@property` derivadas, igual que en Deudas). El modelo se llama `Goal`, **no `Meta`**, para no chocar con la clase interna `class Meta` de Django. La `fecha_limite` es informativa: **no** genera un estado "vencida" (a diferencia de Deudas). `Transaction.goal` es un FK opcional (`on_delete=SET_NULL`).

La lógica vive en `core/services/goals.py` (`apply_transaction_to_goal`, `revert_transaction_from_goal`, `validate_income_against_goal`, `validate_expense_against_goal`, `get_goal_transaction_history`) y se invoca **explícitamente** desde las vistas/ViewSets de Transaction, igual que Deudas (nunca desde señales ni `save()`). Cuando una transacción tiene `goal` asignada:
- `income` → suma a `monto_abonado` (el usuario aporta a su meta).
- `expense` → resta de `monto_abonado` (el usuario retira dinero de la meta).
- `transfer` → no afecta ninguna meta.

Es la relación de signos **invertida** respecto a Deudas (allí el `expense` es el que suma progreso vía `monto_pagado`). Se valida que un aporte no supere el objetivo (`validate_income_against_goal`, bloquea con el máximo abonable) y que un retiro no deje el abonado en negativo (`validate_expense_against_goal`).

### Campo «Asociar a»: una transacción va a una deuda O a una meta, nunca ambas

`Transaction` tiene dos FK opcionales excluyentes: `debt` y `goal` (ambos `SET_NULL`, ambos con `related_name="transactions"`). En la API se exponen como dos campos y `TransactionSerializer.validate()` impide asignar ambos a la vez (además de validar las reglas de la meta). En la web, `TransactionForm` **reemplaza** esos dos campos por un único `ChoiceField` `asociar_a` con optgroups (Deudas/Metas) y valores `debt:<pk>` / `goal:<pk>`; su `clean()` traduce la selección a `instance.debt`/`instance.goal` (el otro a `None`) y dispara la validación correspondiente. Las transferencias fuerzan ambos a `None`. Las vistas/ViewSets de Transaction invocan **ambos** pares de servicios (`..._debt` y `..._goal`) dentro del mismo `db_transaction.atomic()`; cada uno es no-op si su FK es `None`. Al editar o eliminar una transacción, se revierte el efecto anterior sobre deuda y meta antes de aplicar el nuevo.

### Módulo de Contactos: relación bidireccional entre usuarios con filas espejo

`Contact` (`core/models.py`) relaciona dos usuarios registrados (`user` → dueño de la fila, `contact` → el otro usuario; ambos FK a `AUTH_USER_MODEL`). No existe un modelo de "personas" aparte: todo contacto es un `core.User`. La relación es **bidireccional** y se persiste como **filas espejo**: agregar crea 2 filas (A→B y B→A) y eliminar borra ambas, siempre vía `core/services/contacts.py` (`add_contact`/`remove_contact`, con `db_transaction.atomic()`); **nunca** crear/borrar filas `Contact` sueltas, o el espejo queda desincronizado. `add_contact` es idempotente (`get_or_create` + `unique_together (user, contact)`) y rechaza agregarse a sí mismo (`ValidationError`, también en `Contact.clean()`).

Esta forma (una fila por dirección, con `status` propio por fila) es deliberada: cada lista de contactos es un simple `filter(user=...)` que reutiliza `UserOwnedMixin`/`UserOwnedViewSet`, y deja preparados estados asimétricos futuros (solicitud enviada/recibida, bloqueado) — hoy `ContactStatus` solo tiene `CONTACTO`, que es el default.

La búsqueda de usuarios para agregar (`search_users`) filtra por email (`icontains`), excluye al propio usuario y a los contactos existentes, exige mínimo 2 caracteres y limita resultados. La expone la web en `contacts/search/` (`ContactSearchView`, JSON para el autocompletado vanilla JS de `templates/core/contacts/form.html`) y la API como `@action search` de `ContactViewSet`. En la web, agregar no usa un ModelForm: `ContactAddForm` recibe un `contact_id` oculto que puebla el buscador. El módulo Gastos Compartidos (futuro) debe seleccionar participantes únicamente desde esta lista.

### Grupos de contactos: los integrantes son filas `Contact`, no usuarios

`ContactGroup` (`core/models.py`) agrupa contactos de un usuario (familia, viaje, etc.) para los futuros Gastos Compartidos. `members` es un M2M a **`Contact`** (las filas de relación, no `User`) con tabla intermedia explícita `ContactGroupMembership`. Esa elección es deliberada y tiene dos consecuencias que no hay que "arreglar":
- Al eliminar un contacto (vía `remove_contact`, que borra las filas espejo), la BD lo saca de todos los grupos por CASCADE — no existe (ni hace falta) lógica de limpieza.
- El dueño del grupo no puede ser integrante (no es contacto de sí mismo); los módulos consumidores deben tratarlo como participante aparte.

La tabla intermedia explícita existe para poder añadir campos por integrante a futuro (rol, invitación) sin migrar el M2M. `unique_together`: `(user, name)` en el grupo (validado también en `ContactGroupForm.clean_name` y `ContactGroupSerializer.validate_name`, porque la validación de modelo no cubre campos fuera del form) y `(group, contact)` en la membresía. El formulario web usa `ContactMultipleChoiceField` (checkboxes) acotado a los contactos del usuario; la vista sincroniza integrantes con `members.set()` dentro de `atomic()`. Rutas web bajo `contacts/groups/` (names `group_*`); API en `/api/contact-groups/`.

### Gotcha: montos en `style="width:...%"` deben renderizarse sin localización

Los templates que dibujan una barra de progreso (`budgets/list.html`, `debts/list.html`, `debts/detail.html`, `goals/list.html`, `goals/detail.html`) inyectan un `Decimal` calculado (`percent_used`, `percent_paid`, `percent_abonado`) directamente en un atributo `style`. Con `USE_L10N`/locale español activo, Django renderiza esos decimales con coma (`48,500%`), lo cual es un valor CSS inválido y hace que la barra se vea con un ancho incorrecto (llena o casi vacía sin relación con el porcentaje real). La corrección es envolver **solo esa interpolación** con `{% load l10n %}` + `{% localize off %}...{% endlocalize %}` para forzar el punto decimal, dejando el `{{ valor|floatformat:0 }}%` de texto visible (fuera del atributo `style`) con la localización normal. Si se agrega una nueva barra de progreso u otro valor numérico dentro de un atributo `style`/`data-*` consumido por CSS/JS, hay que aplicar el mismo `{% localize off %}` ahí.

### Modelo de autenticación: email como username

`core.User` (`AUTH_USER_MODEL = "core.User"`) define `USERNAME_FIELD = "email"`. Esto tiene efectos en cadena que deben mantenerse sincronizados al tocarlos:
- `core/forms.py: LoginForm` sobreescribe `username` para que sea un `EmailField`.
- `core/api/jwt.py: EmailTokenObtainPairSerializer` sobreescribe `username_field = "email"` para el login vía JWT.
- `core/admin.py: UserAdmin` reordena los `fieldsets` de `BaseUserAdmin` para no mostrar el email dos veces.

### El JWT es opcional y depende de un setting

`JWT_ENABLED` (desde `.env`, por defecto `True`) controla si `rest_framework_simplejwt.authentication.JWTAuthentication` se inserta en `REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]` y si las rutas `/api/auth/token/` y `/api/auth/token/refresh/` se registran en `core/api/urls.py`. La autenticación por sesión (`SessionAuthentication`) siempre está disponible, sin importar este flag.

### Estructura de rutas

- `fintrack/urls.py` monta: `/admin/`, `core.urls` en `/` (interfaz web), `core.api.urls` en `/api/` (REST), además del schema/Swagger/Redoc de drf-spectacular bajo `/api/schema/`, `/api/docs/`, `/api/redoc/`.
- `core/api/urls.py` usa un `DefaultRouter` de DRF para los seis ViewSets (cuentas, categorías, etiquetas, presupuestos, **deudas**, transacciones) más rutas explícitas para registro/perfil/dashboard/reporte-pdf y (condicionalmente) JWT.
- `core/urls.py` (web) sigue el mismo patrón de 5 rutas por módulo CRUD (`list`/`create`/`detail`/`update`/`delete`) para cuentas, categorías, presupuestos y deudas (`debts/`, `debts/new/`, `debts/<int:pk>/`, `debts/<int:pk>/edit/`, `debts/<int:pk>/delete/`).
- Las `@action` personalizadas en `TransactionViewSet` (`core/api/views.py`) exponen `reconcile`, `unreconcile`, `upload_attachment`, `export_csv`, `import_csv` como subrutas, replicando las vistas web equivalentes en `core/urls.py`. `DebtViewSet` expone `@action(detail=True) transactions` para listar el historial de transacciones de una deuda (equivalente al contexto que arma `DebtDetailView` para la web).

### Formato de import/export CSV

Columnas esperadas: `date,account,category,transaction_type,amount,description,notes` (ver `CSV_HEADERS` en `core/services/csv_io.py`). Cuenta/categoría se emparejan por nombre dentro de los propios registros del usuario que importa; una cuenta sin coincidencia hace que la fila falle (nunca se crea silenciosamente), una categoría sin coincidencia se deja en null.

## Estilo de código

- Se aplica con `black` (line length 88), `isort` (profile `black`, `known_first_party = ["core", "fintrack"]`) y `flake8` (`.flake8`: max-line-length 88, ignora `E203,W503`) — todos conectados en `.pre-commit-config.yaml`.
- Los textos de cara al usuario (labels de formularios, `verbose_name` de modelos, mensajes de éxito) están en español; mantén ese criterio en texto nuevo.
