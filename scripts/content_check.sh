#!/usr/bin/env bash
# Report tracked files whose text contains a word the owner keeps out of public
# text. Usage: scripts/content_check.sh DENYLIST_FILE
#
# The word list lives OUTSIDE the repository (a file locally, a secret in CI), so
# committing this script publishes nothing. Its format:
#   deny:<word>     matched case-insensitively anywhere in a line, including
#                   inside identifiers (`word_name` is a hit; a whole-word match
#                   would miss it, because `_` is a word character)
#   allow:<ERE>     removed from each line before matching, e.g. a hostname that
#                   may appear while the word it contains may not
#
# Exit 0 clean, 1 hits, 2 no usable list. A hit prints `path:line` and never the
# word: CI logs on a public repository are public, and echoing a match would
# publish the list this check exists to protect.
#
# Exempt: docs/superpowers/ (dated history, never edited) and lockfiles.
set -euo pipefail

list="${1:-}"
if [[ -z "$list" || ! -f "$list" ]]; then
  echo "content_check: no denylist file at '${list:-<none given>}'" >&2
  exit 2
fi

deny="$(mktemp)"
trap 'rm -f "$deny"' EXIT
sed -n 's/^deny://p' "$list" | sed '/^[[:space:]]*$/d' > "$deny"
if [[ ! -s "$deny" ]]; then
  echo "content_check: '$list' has no deny: lines; refusing to report clean" >&2
  exit 2
fi

# `s/^//` is a no-op that keeps the sed call valid when there are no allow lines.
strip=(-e 's/^//')
while IFS= read -r pattern; do
  [[ -n "$pattern" ]] && strip+=(-e "s#${pattern}##g")
done < <(sed -n 's/^allow://p' "$list")

status=0
while IFS= read -r -d '' file; do
  case "$file" in
    docs/superpowers/*|uv.lock|web/package-lock.json) continue ;;
  esac
  [[ -f "$file" ]] || continue
  # Text files only: `grep -I` treats a binary file as matching nothing.
  grep -Iq . "$file" 2>/dev/null || continue
  lines="$(sed -E "${strip[@]}" "$file" | grep -niF -f "$deny" | cut -d: -f1 || true)"
  if [[ -n "$lines" ]]; then
    while IFS= read -r n; do echo "$file:$n"; done <<< "$lines"
    status=1
  fi
done < <(git ls-files -z)

exit "$status"
