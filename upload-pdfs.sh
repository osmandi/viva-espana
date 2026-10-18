#!/usr/bin/env bash
# Sube todos los PDFs de una carpeta al endpoint POST /pdf/extract de la API.
# Cada PDF se ELIMINA de la carpeta una vez procesado con éxito; los que
# fallan se conservan para poder reintentar.
#
# Uso: ./upload-pdfs.sh <carpeta> [url-base]
#   carpeta   Carpeta con los PDFs (no recursivo)
#   url-base  URL de la API (por defecto: http://localhost:8000)

# Si se invoca con `sh upload-pdfs.sh`, relanza con bash
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

if [[ $# -lt 1 || "$1" == "-h" || "$1" == "--help" ]]; then
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
fi

DIR="$1"
BASE_URL="${2:-http://localhost:8000}"

[[ -d "$DIR" ]] || { echo "No existe la carpeta: $DIR" >&2; exit 1; }
command -v curl >/dev/null || { echo "Falta 'curl' en el PATH" >&2; exit 1; }

shopt -s nullglob nocaseglob
files=("$DIR"/*.pdf)
shopt -u nocaseglob

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No hay PDFs en $DIR" >&2
  exit 1
fi

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

ok=0
failed=0
for file in "${files[@]}"; do
  name=$(basename "$file")
  printf '%s ... ' "$name"
  # El análisis puede tardar: tiempo máximo de 5 minutos por archivo
  status=$(curl -sS --max-time 300 -o "$tmp" -w '%{http_code}' \
    -X POST "$BASE_URL/pdf/extract" \
    -F "file=@$file;type=application/pdf" 2>&1) || status="error"
  if [[ "$status" == "200" ]]; then
    echo "OK ($(jq -r '"\(.pages) páginas -> \(.blob)"' "$tmp" 2>/dev/null || echo subido))"
    rm -- "$file"
    ok=$((ok + 1))
  else
    echo "FALLÓ ($status) $(head -c 200 "$tmp" 2>/dev/null || true)"
    failed=$((failed + 1))
  fi
done

echo
echo "Correctos: $ok | Fallidos: $failed"
[[ "$failed" -eq 0 ]]
