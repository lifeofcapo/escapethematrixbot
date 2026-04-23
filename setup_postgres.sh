#!/bin/bash

# Запуск: bash setup_postgres.sh

set -e

DB_USER="vpnbot"
DB_NAME="vpnbot"
DB_PASS=$(openssl rand -base64 18 | tr -dc 'a-zA-Z0-9' | head -c 24)

echo "▶ Устанавливаю PostgreSQL..."
apt-get update -qq
apt-get install -y postgresql postgresql-contrib

echo "▶ Создаю пользователя и базу данных..."
sudo -u postgres psql << SQL
CREATE USER $DB_USER WITH PASSWORD '$DB_PASS';
CREATE DATABASE $DB_NAME OWNER $DB_USER;
GRANT ALL PRIVILEGES ON DATABASE $DB_NAME TO $DB_USER;
SQL

echo "▶ Разрешаю внешние подключения (для Vercel)..."
PG_CONF=$(sudo -u postgres psql -t -c "SHOW config_file;" | tr -d ' ')
PG_HBA=$(dirname $PG_CONF)/pg_hba.conf

# Слушать все интерфейсы
sed -i "s/#listen_addresses = 'localhost'/listen_addresses = '*'/" $PG_CONF

# Разрешить подключения по паролю снаружи
echo "host    $DB_NAME    $DB_USER    0.0.0.0/0    scram-sha-256" >> $PG_HBA

systemctl restart postgresql
systemctl enable postgresql

echo ""
echo "✅ PostgreSQL настроен!"
echo ""
echo "Добавь в .env файл бота:"
echo "  DATABASE_URL=postgresql://$DB_USER:$DB_PASS@localhost:5432/$DB_NAME"
echo ""
echo "Для Vercel используй внешний IP сервера вместо localhost:"
echo "  DATABASE_URL=postgresql://$DB_USER:$DB_PASS@<VPS_IP>:5432/$DB_NAME"
echo ""
echo "⚠️  Не забудь открыть порт 5432 в firewall для IP Vercel!"
echo "   ufw allow from <VERCEL_IP_RANGE> to any port 5432"