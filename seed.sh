#!/usr/bin/env bash
#
# seed.sh — (Re)seed the JCI NEXO demo database.
#
# Runs the demo seeder against the backend's SQLite database from anywhere,
# so you don't have to "cd backend … ; cd ..". Equivalent to:
#
#     cd backend && MEMBER_TRACKER_DB_PATH=member_tracker.db python3 seed_demo.py && cd ..
#
# Usage:
#   ./seed.sh
#
# Override the database path if you like:
#   MEMBER_TRACKER_DB_PATH=/tmp/nexo.db ./seed.sh
#
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"

DB_PATH="${MEMBER_TRACKER_DB_PATH:-member_tracker.db}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 not found. Install Python 3 to run the seeder."; exit 1
fi

echo "▶  Seeding demo data into $BACKEND_DIR/$DB_PATH ..."
( cd "$BACKEND_DIR" && MEMBER_TRACKER_DB_PATH="$DB_PATH" python3 seed_demo.py )
echo "✅ Done."
