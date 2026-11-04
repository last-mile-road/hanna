#!/usr/bin/env bash
# Build the local OSM place gazetteer for a country (tier 2 of the location cascade).
#
# Downloads the Geofabrik extract and imports it with osm2pgsql into a separate
# database (hanna_osm by default). Only place nodes/polygons are queried, so the
# classic pgsql output with hstore is enough — no Nominatim install needed.
#
# Usage:
#   tools/build_osm_extract.sh mozambique
#   OSM_DATABASE_URL=... tools/build_osm_extract.sh nigeria
#
# Re-run when the extract's age matters (Geofabrik updates daily).

set -euo pipefail

COUNTRY="${1:?country slug, e.g. mozambique}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA="$ROOT/data/osm"
PBF="$DATA/${COUNTRY}-latest.osm.pbf"
DB="${OSM_DB:-hanna_osm}"
PGHOST="${PGHOST:-127.0.0.1}"
PGPORT="${PGPORT:-55432}"
PGUSER="${PGUSER:-sam}"

mkdir -p "$DATA"
if [ ! -f "$PBF" ]; then
  echo "downloading ${COUNTRY} extract…"
  curl -sL -o "$PBF" "https://download.geofabrik.de/africa/${COUNTRY}-latest.osm.pbf"
fi
ls -lh "$PBF"

export PGHOST PGPORT PGUSER
psql -d postgres -tc "SELECT 1 FROM pg_database WHERE datname = '$DB'" | grep -q 1 \
  || createdb "$DB"
psql -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS postgis; CREATE EXTENSION IF NOT EXISTS hstore;"

# Import once. Re-running over an existing import needs --append (updates) or a
# fresh database; this script targets a fresh build.
osm2pgsql -d "$DB" --hstore -O pgsql --number-processes 4 "$PBF"

echo "done. check:"
echo "  psql -d $DB -c \"select place, count(*) from planet_osm_point where place is not null group by place;\""
echo "  HANNA reads OSM_DATABASE_URL (default postgresql+pg8000://$PGUSER@$PGHOST:$PGPORT/$DB)"
