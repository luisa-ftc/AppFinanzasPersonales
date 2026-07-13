# FinTrack - Setup script (Windows)
$ErrorActionPreference = "Stop"

Write-Host "Creating virtual environment..."
python -m venv .venv

Write-Host "Activating and installing dependencies..."
& .\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Write-Host "Running migrations..."
python manage.py migrate

Write-Host "Loading demo data..."
python manage.py setup_demo

Write-Host ""
Write-Host "Setup complete! Next steps:"
Write-Host "  1. python manage.py createsuperuser"
Write-Host "  2. python manage.py runserver"
