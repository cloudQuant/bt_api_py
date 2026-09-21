#!/usr/bin/env bash
# Stage root-repository gitlinks from the current HEAD of each direct submodule.
# Intended for release preparation after every child repository has been pushed.

set -euo pipefail

usage() {
    printf 'Usage: %s [--check]\n' "${0##*/}" >&2
    exit 2
}

MODE="stage"
case "${1:-}" in
    "") ;;
    --check) MODE="check" ;;
    *) usage ;;
esac

ROOT="$(git -C "$(dirname "${BASH_SOURCE[0]}")/.." rev-parse --show-toplevel)"
declare -a SUBMODULES=()

while IFS= read -r key_and_path; do
    path="${key_and_path#* }"
    [ -n "$path" ] && SUBMODULES+=("$path")
done < <(git -C "$ROOT" config --file .gitmodules --get-regexp '^submodule\..*\.path$')

[ "${#SUBMODULES[@]}" -gt 0 ] || {
    printf 'ERROR: no submodules are declared in %s/.gitmodules\n' "$ROOT" >&2
    exit 1
}

echo "== Preflight: ${#SUBMODULES[@]} direct submodules =="
for path in "${SUBMODULES[@]}"; do
    if [ ! -e "$ROOT/$path/.git" ]; then
        printf 'ERROR: submodule is not initialized: %s\n' "$path" >&2
        printf 'Run: git submodule update --init --recursive\n' >&2
        exit 1
    fi
done

recursive_status="$(git -C "$ROOT" submodule status --recursive)"
if printf '%s\n' "$recursive_status" | grep -q '^-' ; then
    printf 'ERROR: one or more recursive submodules are not initialized:\n%s\n' \
        "$recursive_status" >&2
    exit 1
fi
if printf '%s\n' "$recursive_status" | grep -q '^U' ; then
    printf 'ERROR: a submodule has an unresolved gitlink conflict:\n%s\n' \
        "$recursive_status" >&2
    exit 1
fi

if ! git -C "$ROOT" submodule foreach --recursive --quiet '
    status="$(git status --porcelain)"
    if test -n "$status"; then
        printf "ERROR: %s has uncommitted changes:\n%s\n" "$displaypath" "$status" >&2
        exit 1
    fi
'; then
    printf 'ERROR: gitlinks were not updated; commit or stash child changes first.\n' >&2
    exit 1
fi

if [ "$MODE" = "check" ]; then
    echo "== Proposed gitlink changes (nothing staged) =="
    git -C "$ROOT" diff --submodule=log -- "${SUBMODULES[@]}"
    exit 0
fi

for path in "${SUBMODULES[@]}"; do
    git -C "$ROOT" add -- "$path"
done

if git -C "$ROOT" diff --cached --quiet -- "${SUBMODULES[@]}"; then
    echo "== Gitlinks already match the current child HEADs; nothing staged =="
    exit 0
fi

echo "== Staged gitlink changes =="
git -C "$ROOT" diff --cached --submodule=log -- "${SUBMODULES[@]}"
echo "== Review the staged diff, then commit it in the root repository =="
