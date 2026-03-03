#!/usr/bin/env bash
set -euo pipefail

# Delete .log files located directly under the repository root.
# Usage: ./scripts/clear_root_logs.sh [target_dir]
# Default target_dir is the repository root resolved relative to this script.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
TARGET_DIR="${1:-${ROOT_DIR}}"

if [[ ! -d "${TARGET_DIR}" ]]; then
  echo "Target directory does not exist: ${TARGET_DIR}" >&2
  exit 1
fi

# Only operate on files in the target directory (no recursion).
mapfile -d '' LOG_FILES < <(find "${TARGET_DIR}" -maxdepth 1 -type f -name '*.log' -print0)

if [[ ${#LOG_FILES[@]} -eq 0 ]]; then
  echo "No .log files found in ${TARGET_DIR}"
  exit 0
fi

for file in "${LOG_FILES[@]}"; do
  rm -f "${file}"
  echo "Removed: ${file}"
done

echo "Done. Deleted ${#LOG_FILES[@]} log file(s) in ${TARGET_DIR}"
