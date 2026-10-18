#!/usr/bin/env bash
# Lista los modelos de Microsoft Foundry cubiertos por la cuota actual
# (límite > 0) en una región, con su cuota usada y disponible.
#
# La cuota es por tipo de despliegue (SKU): un modelo puede tener cuota en
# DataZoneStandard y 0 en GlobalStandard (el tipo por defecto en Foundry).
#
# Uso: ./models-quota.sh [región] [--all] [--batch]
#   región   Región de Azure (por defecto: la de variables.tf, o eastus2)
#   --all    Incluye también los SKUs sin cuota (límite = 0)
#   --batch  Incluye los SKUs Batch (cuota en miles de tokens en cola,
#            no válidos para inferencia en tiempo real)

# Si se invoca con `sh models-quota.sh`, relanza con bash
[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"

set -euo pipefail

LOCATION=""
SHOW_ALL=false
SHOW_BATCH=false
for arg in "$@"; do
  case "$arg" in
    --all) SHOW_ALL=true ;;
    --batch) SHOW_BATCH=true ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) LOCATION="$arg" ;;
  esac
done

if [[ -z "$LOCATION" ]]; then
  LOCATION=$(awk '/variable "location"/{f=1} f&&/default/{gsub(/[" ]/,"",$3); print $3; exit}' \
    "$(dirname "$0")/variables.tf" 2>/dev/null || true)
  LOCATION=${LOCATION:-eastus2}
fi

for cmd in az jq; do
  command -v "$cmd" >/dev/null || { echo "Falta '$cmd' en el PATH" >&2; exit 1; }
done
az account show >/dev/null 2>&1 || { echo "Ejecuta 'az login' primero" >&2; exit 1; }

SUB=$(az account show --query name -o tsv)
echo "Suscripción: $SUB | Región: $LOCATION" >&2

usage=$(az cognitiveservices usage list -l "$LOCATION" -o json)
models=$(az cognitiveservices model list -l "$LOCATION" -o json)

# Une cada SKU de cada modelo con su cuota mediante usageName == name.value
# y clasifica cada modelo por función según sus capabilities:
#   rerank    -> "rerank" en el nombre (no hay capability propia; Azure los
#                marca como chatCompletion)
#   embedding -> capabilities.embeddings, o "embed" en el nombre (los de Cohere
#                no declaran la capability)
#   chat      -> capabilities.chatCompletion (excepto model-router)
# Los modelos que no encajan en ninguna (imagen, audio, router...) se omiten.
rows=$(jq -r --argjson all "$SHOW_ALL" --argjson batch "$SHOW_BATCH" --argjson usage "$usage" '
  ($usage | map({key: .name.value, value: {limit, used: .currentValue}}) | from_entries) as $q
  | [ .[] | .model as $m | ($m.capabilities // {}) as $c
      | ( if $m.name | test("rerank"; "i") then "rerank"
          elif $c.embeddings == "true" or ($m.name | test("embed"; "i")) then "embedding"
          elif $c.chatCompletion == "true" and $c.router != "true" then "chat"
          else empty end ) as $group
      | $m.skus[]?
      | select(.usageName != null and $q[.usageName] != null)
      | select($batch or (.name | test("Batch$") | not))
      | { group: $group, model: $m.name, version: $m.version, sku: .name,
          limit: $q[.usageName].limit, used: $q[.usageName].used,
          status: $m.lifecycleStatus } ]
  | map(select($all or .limit > 0))
  | unique_by([.group, .model, .version, .sku]) | sort_by(.model, .version, .sku)
  | .[] | [.group, .model, .version, .sku, .status, .limit, .used, (.limit - .used)] | @tsv
' <<<"$models")

for group in chat embedding rerank; do
  case "$group" in
    chat)      title="CHAT" ;;
    embedding) title="EMBEDDING" ;;
    rerank)    title="RERANK" ;;
  esac
  group_rows=$(awk -F'\t' -v g="$group" '$1 == g' <<<"$rows" | cut -f2-)
  count=$(grep -c . <<<"$group_rows" || true)
  echo
  echo "== $title ($count) =="
  if [[ "$count" -eq 0 ]]; then
    echo "(sin modelos con cuota)"
    continue
  fi
  { printf 'MODELO\tVERSIÓN\tSKU\tESTADO\tLÍMITE\tUSADO\tDISPONIBLE\n'; echo "$group_rows"; } \
    | column -t -s $'\t'
done
