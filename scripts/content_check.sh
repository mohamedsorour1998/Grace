#!/usr/bin/env bash
# Report tracked files whose text contains a word the owner keeps out of public
# text. Usage: scripts/content_check.sh DENYLIST_FILE
#
# The word list lives OUTSIDE the repository (a file locally, a secret in CI), so
# committing this script publishes nothing. Its format, one entry per line:
#   deny:<word>     matched case-insensitively anywhere in a line, including
#                   inside identifiers (`word_name` is a hit; a whole-word match
#                   would miss it, because `_` is a word character)
#   allow:<ERE>     removed from each line before matching, e.g. a hostname that
#                   may appear while the word it contains may not
# Surrounding whitespace and a trailing `\r` are ignored, because a list pasted
# into a CI secret easily gains either and would then silently match nothing.
#
# Exit 0 clean, 1 hits, 2 when the check cannot be trusted to have run: no list,
# no deny: lines, an allow: pattern sed rejects, or a file it cannot read. A hit
# prints `path:line` and never the word: CI logs on a public repository are
# public, and echoing a match (or a pattern) would publish the list this check
# exists to protect.
#
# Exempt: docs/superpowers/ (dated history, never edited) and lockfiles.
set -euo pipefail
# Byte semantics everywhere, so GNU and BSD tools agree: in a UTF-8 locale GNU
# grep treats invalid UTF-8 as binary and `-I` would skip the file unscanned.
export LC_ALL=C

list="${1:-}"
if [[ -z "$list" || ! -f "$list" ]]; then
  echo "content_check: no denylist file at '${list:-<none given>}'" >&2
  exit 2
fi
# Absolute before the `cd` below, so a relative path keeps meaning what it meant.
list="$(cd "$(dirname "$list")" && pwd)/$(basename "$list")"

# The whole repository, whichever directory this runs from — otherwise only the
# current subtree is scanned and the docs/superpowers/ exemption stops matching.
cd "$(git rev-parse --show-toplevel)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
deny="$work/deny"
: > "$deny"
# The SOH byte as the sed delimiter, so an allow pattern may contain `#` or `/`.
d=$'\001'
strip=(-e 's/^//')  # a no-op keeps the sed call valid when there are no allow lines

trim() {
  local s="${1%$'\r'}"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}

# `|| [[ -n "$line" ]]` keeps a last line that has no trailing newline.
while IFS= read -r line || [[ -n "$line" ]]; do
  line="$(trim "$line")"
  case "$line" in
    deny:*)
      word="$(trim "${line#deny:}")"
      [[ -n "$word" ]] && printf '%s\n' "$word" >> "$deny"
      ;;
    allow:*)
      pattern="$(trim "${line#allow:}")"
      [[ -n "$pattern" ]] && strip+=(-e "s${d}${pattern}${d}${d}g")
      ;;
  esac
done < "$list"

if [[ ! -s "$deny" ]]; then
  echo "content_check: the denylist has no deny: lines; refusing to report clean" >&2
  exit 2
fi
# Compile every allow pattern once, up front. A pattern sed rejects would
# otherwise fail on every file, and the check would report clean. stderr is
# discarded because sed's message quotes the pattern.
if ! sed -E "${strip[@]}" </dev/null >/dev/null 2>&1; then
  echo "content_check: an allow: pattern is not a valid extended regex" >&2
  exit 2
fi

status=0
stripped="$work/stripped"
# The file list arrives on FD 3, so nothing inside the loop can read it from
# stdin; every path is passed as `./path`, so a name starting with `-` (or a
# file named `-`) is a file, never an option or stdin.
while IFS= read -r -d '' file <&3; do
  case "$file" in
    docs/superpowers/*|uv.lock|web/package-lock.json) continue ;;
  esac
  [[ -f "./$file" ]] || continue
  # Text files only: `grep -I` treats a file containing NUL bytes as binary.
  grep -Iq . "./$file" </dev/null 2>/dev/null || continue
  if ! sed -E "${strip[@]}" "./$file" > "$stripped" 2>/dev/null; then
    echo "content_check: could not read $file" >&2
    exit 2
  fi
  # grep: 0 found, 1 none, anything else an error that must not read as clean.
  rc=0
  grep -niF -f "$deny" "$stripped" > "$work/hits" || rc=$?
  if [[ $rc -eq 0 ]]; then
    cut -d: -f1 "$work/hits" | while IFS= read -r n; do echo "$file:$n"; done
    status=1
  elif [[ $rc -ne 1 ]]; then
    echo "content_check: grep failed on $file" >&2
    exit 2
  fi
done 3< <(git ls-files -z)

exit "$status"
