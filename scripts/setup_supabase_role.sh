#!/bin/bash
# Observibot — Create a read-only monitoring role on Supabase
#
# Prerequisites:
#   - Supabase CLI installed and linked to your project
#   - Run from the directory where `supabase link` was executed
#
# Usage:
#   1. Replace YOUR_STRONG_PASSWORD_HERE with a secure random password
#   2. Run: bash scripts/setup_supabase_role.sh
#   3. Copy the connection string printed at the end into your .env

set -e

ROLE_PASSWORD="YOUR_STRONG_PASSWORD_HERE"
SUPABASE_HOST="YOUR_REGION.pooler.supabase.com"
PROJECT_REF="YOUR_PROJECT_REF"

echo "Creating observibot_reader role..."

supabase db query --linked "
CREATE ROLE observibot_reader WITH LOGIN PASSWORD '${ROLE_PASSWORD}';
GRANT CONNECT ON DATABASE postgres TO observibot_reader;
GRANT USAGE ON SCHEMA public TO observibot_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO observibot_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO observibot_reader;
GRANT pg_monitor TO observibot_reader;
"

echo ""
echo "Done. Add this to your Observibot .env:"
echo ""
echo "SUPABASE_DB_URL=postgresql://observibot_reader.${PROJECT_REF}:${ROLE_PASSWORD}@${SUPABASE_HOST}:5432/postgres"
