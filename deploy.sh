#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/var/www/gdh-cripto}"
BRANCH="${BRANCH:-main}"
REMOTE="${REMOTE:-origin}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
DB_FILE="${DB_FILE:-$PROJECT_DIR/db.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
SERVICES="${SERVICES:-gdh-cripto.service gunicorn-gdh.service}"
NO_RESTART=0

if [[ "${1:-}" == "--no-restart" ]]; then
    NO_RESTART=1
fi

echo "==> Entrando no projeto: $PROJECT_DIR"
cd "$PROJECT_DIR"

echo "==> Criando backup do banco da nuvem"
mkdir -p "$BACKUP_DIR"
if [[ -f "$DB_FILE" ]]; then
    BACKUP_FILE="$BACKUP_DIR/db.sqlite3.$(date +%Y%m%d-%H%M%S).backup"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "Backup criado: $BACKUP_FILE"
else
    echo "Aviso: banco SQLite nao encontrado em $DB_FILE. Pulando backup."
fi

echo "==> Ativando ambiente virtual"
if [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
else
    echo "Erro: ambiente virtual nao encontrado em $VENV_DIR"
    exit 1
fi

echo "==> Atualizando codigo"
git fetch "$REMOTE" "$BRANCH"
git pull "$REMOTE" "$BRANCH"

echo "==> Aplicando migrations"
python3 manage.py migrate

echo "==> Coletando arquivos estaticos"
python3 manage.py collectstatic --noinput

if [[ "$NO_RESTART" == "1" ]]; then
    echo "==> Reinicio de servicos pulado por --no-restart"
    exit 0
fi

echo "==> Reiniciando servicos"
for service in $SERVICES; do
    echo "Reiniciando $service"
    systemctl restart "$service"
done

echo "==> Status dos servicos"
for service in $SERVICES; do
    systemctl --no-pager --lines=8 status "$service"
done

echo "Deploy finalizado."
