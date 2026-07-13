#!/usr/bin/env bash
set -euo pipefail

echo "Creating virtual environment..."
python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example"
fi

python manage.py migrate
python manage.py setup_demo

echo ""
echo "Setup complete! Next steps:"
echo "  1. python manage.py createsuperuser"
echo "  2. python manage.py runserver"
