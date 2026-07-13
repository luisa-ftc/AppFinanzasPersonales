# FinTrack

Aplicación web de gestión financiera personal construida con **Django 4.x** y **Django REST Framework**.

## Características

- Autenticación con usuario extendido (`AbstractUser`, email único)
- Registro, login, logout y recuperación de contraseña por email
- API REST con JWT opcional
- Modelos: User, Account, Category, Transaction, Budget, Tag, Attachment
- Dashboard con saldos, gráficas Chart.js (ingresos vs gastos, distribución por categoría)
- CRUD completo para cuentas, categorías, presupuestos y transacciones
- Importación/exportación CSV con detección de duplicados por hash
- Adjuntos en transacciones con validación de tipo y tamaño
- Reconciliación de transacciones
- Reportes CSV y PDF
- Documentación API con Swagger y ReDoc
- Tests, linting (black, isort, flake8) y pre-commit

## Requisitos

- Python 3.11+
- pip

## Instalación rápida (Windows PowerShell)

```powershell
cd Projects\fintrack
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python manage.py migrate
python manage.py setup_demo
python manage.py createsuperuser
python manage.py runserver
```

`setup_demo` crea (si no existe) el usuario **demo@fintrack.local** / **demo1234**, junto con cuentas, categorías, un presupuesto y transacciones de ejemplo. Es idempotente: puedes correrlo varias veces sin duplicar datos.

## Instalación (Linux/macOS)

```bash
cd Projects/fintrack
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py setup_demo
python manage.py createsuperuser
python manage.py runserver
```

## Scripts de ayuda

```powershell
# Windows
.\scripts\setup.ps1
.\scripts\run_dev.ps1
```

```bash
# Linux/macOS
chmod +x scripts/setup.sh scripts/run_dev.sh
./scripts/setup.sh
./scripts/run_dev.sh
```

## Variables de entorno (`.env`)

| Variable | Descripción |
| --- | --- |
| `SECRET_KEY` | Clave secreta de Django. Cambiar por un valor aleatorio propio en producción. |
| `DEBUG` | Modo debug de Django (`True`/`False`). |
| `ALLOWED_HOSTS` | Hosts permitidos, separados por coma. |
| `CSRF_TRUSTED_ORIGINS` | Orígenes de confianza para CSRF, separados por coma. |
| `DATABASE_URL` | **No tiene efecto todavía**: la base de datos está fija a SQLite (`db.sqlite3`) en `fintrack/settings.py`. Esta variable queda reservada para una futura migración a otro motor. |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | Configuración de envío de correo (recuperación de contraseña). En desarrollo se usa el backend de consola por defecto. |
| `SITE_NAME`, `SITE_URL` | Nombre y URL del sitio, usados en plantillas de email. |
| `JWT_ENABLED` | Habilita/deshabilita la autenticación JWT en la API (`True`/`False`). La autenticación por sesión siempre está disponible. |
| `JWT_ACCESS_TOKEN_LIFETIME_MINUTES` | Minutos de vida del access token JWT. |
| `JWT_REFRESH_TOKEN_LIFETIME_DAYS` | Días de vida del refresh token JWT. |
| `MAX_ATTACHMENT_SIZE_MB` | Tamaño máximo permitido para adjuntos de transacciones. |
| `ALLOWED_ATTACHMENT_TYPES` | Tipos MIME permitidos para adjuntos, separados por coma. |

> **CORS**: `CORS_ALLOWED_ORIGINS` no se lee desde `.env` — está definido directamente en `fintrack/settings.py`. Si vas a integrar un frontend en otro dominio, edita ese setting.

## Solución de problemas

- **PowerShell no permite activar el entorno virtual** (`Activate.ps1` bloqueado):
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  .\.venv\Scripts\Activate.ps1
  python --version
  ```
- **Linux/macOS: "Permission denied" al ejecutar los scripts**: dales permisos de ejecución antes de correrlos:
  ```bash
  chmod +x scripts/setup.sh scripts/run_dev.sh
  ```

## URLs principales

| Recurso | URL |  |  |  |
| --- | --- | --- | --- | --- |
| Dashboard | http://127.0.0.1:8000/ |  |  |  |
| Admin | http://127.0.0.1:8000/admin/ |  |  |  |
| Swagger API | http://127.0.0.1:8000/api/docs/ |  |  |  |
| ReDoc API | http://127.0.0.1:8000/api/redoc/ |  |  |  |
| Schema OpenAPI | http://127.0.0.1:8000/api/schema/ |  |  |  |

## API JWT (opcional)

Habilitado por defecto (`JWT_ENABLED=True` en `.env`).

```bash
# Obtener token (usar email como username; con el usuario creado por setup_demo)
curl -X POST http://127.0.0.1:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email": "demo@fintrack.local", "password": "demo1234"}'

# Usar token
curl http://127.0.0.1:8000/api/accounts/ \
  -H "Authorization: Bearer <access_token>"
```

## Paginación y filtros de la API

Los listados de la API usan paginación por número de página, con 20 elementos por página (`PAGE_SIZE = 20`). La respuesta incluye `count`, `next`, `previous` y `results`.

El endpoint de transacciones (`/api/transactions/`) además soporta:

- **Filtros exactos**: `?account=<id>`, `?category=<id>`, `?transaction_type=income|expense|transfer`, `?is_reconciled=true|false`, `?date=YYYY-MM-DD`.
- **Búsqueda de texto**: `?search=<texto>` (busca en descripción y notas).
- **Ordenamiento**: `?ordering=date` / `?ordering=-amount` / `?ordering=created_at`.

## Importación CSV

Formato esperado:

```csv
date,account,category,transaction_type,amount,description,notes
2026-03-01,Cuenta Principal,Comida,expense,150.00,Supermercado,
```

Los duplicados se detectan por hash SHA-256 de fecha + cuenta + monto + descripción.

## Tests y calidad de código

```bash
pytest
black .
isort .
flake8 .
pre-commit install
pre-commit run --all-files
```

> Actualmente no hay medición de cobertura configurada (`pytest-cov` no está en `requirements.txt`).

## Despliegue / producción

Antes de desplegar en producción:

1. Genera un `SECRET_KEY` propio y aleatorio.
2. Configura `DEBUG=False`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` con tus dominios reales.
3. Ejecuta `python manage.py collectstatic` para recolectar los estáticos en `STATIC_ROOT`.
4. Sirve la app con un servidor WSGI/ASGI real (por ejemplo Gunicorn apuntando a `fintrack.wsgi:application`, o Uvicorn/Daphne a `fintrack.asgi:application`), no con `runserver`.

## Estructura del proyecto

```
fintrack/
├── fintrack/          # Configuración Django
├── core/              # App principal
│   ├── api/           # REST API
│   ├── services/      # Lógica de negocio
│   ├── fixtures/      # Datos de ejemplo
│   └── tests/
├── templates/
├── static/
├── scripts/
├── requirements.txt
└── manage.py
```

## Licencia

MIT (pendiente añadir el archivo `LICENSE` en la raíz del repositorio).