#!/usr/bin/env bash
# Count all code lines in tracked repository files.
set -euo pipefail

cd "$(dirname "$0")" || exit 1

echo "nanobot code line count"
echo "================================"
echo ""

declare -A line_counts=()
declare -A file_counts=()
total_lines=0
total_files=0

while IFS= read -r -d '' file; do
  kind=""
  case "$file" in
    *.py)   kind="py" ;;
    *.ts)   kind="ts" ;;
    *.js)   kind="js" ;;
    *.sh)   kind="sh" ;;
    *.json) kind="json" ;;
    *.toml) kind="toml" ;;
    *.yml|*.yaml) kind="yaml" ;;
    *.ps1)  kind="ps1" ;;
    *.bat)  kind="bat" ;;
    *.cmd)  kind="cmd" ;;
    *.vbs)  kind="vbs" ;;
    Dockerfile|*/Dockerfile) kind="dockerfile" ;;
    *) continue ;;
  esac

  lines=$(wc -l < "$file")
  line_counts["$kind"]=$((line_counts["$kind"] + lines))
  file_counts["$kind"]=$((file_counts["$kind"] + 1))
  total_lines=$((total_lines + lines))
  total_files=$((total_files + 1))
done < <(git ls-files -z)

if [[ "$total_files" -eq 0 ]]; then
  echo "No code files found."
  exit 0
fi

for kind in "${!line_counts[@]}"; do
  printf "%s\t%s\t%s\n" "$kind" "${file_counts[$kind]}" "${line_counts[$kind]}"
done | sort -k3,3nr | while IFS=$'\t' read -r kind files lines; do
  printf "  %-12s %4s files %8s lines\n" "$kind" "$files" "$lines"
done

echo ""
printf "  %-12s %4s files %8s lines\n" "TOTAL" "$total_files" "$total_lines"
