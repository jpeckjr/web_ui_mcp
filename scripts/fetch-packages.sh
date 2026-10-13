#!/usr/bin/env bash
# fetch-packages.sh — copy IICS export packages from the EC2 box to this Mac
# so they can be attached to a Jira change ticket.
#
# Usage:
#   ./scripts/fetch-packages.sh <TICKET> <package.zip> [<package.zip> ...]
#
# Example:
#   ./scripts/fetch-packages.sh DP-4100 \
#     prod_mt_stage_salesforce_contract-1791600000000_AbC.zip \
#     dev_mt_stage_salesforce_contract-1791554783045_7L7IVqXeLwAlVDdRfQbEuv.zip
#
# What it does, per package:
#   1. Copies the ZIP and its .manifest.json from idmc-box:~/informatica-mcp-exports/
#      into runs/<TICKET>/ next to this repo.
#   2. Verifies the ZIP's SHA-256 matches the manifest, so what gets attached
#      to Jira is byte-for-byte what export_assets produced.
#   3. Prints the absolute paths to pass to jira_update_issue(attachments=...).
#
# Read-only on the box; writes only under runs/<TICKET>/ locally.

set -euo pipefail

BOX="${IDMC_BOX:-idmc-box}"
REMOTE_DIR="${IDMC_EXPORT_DIR:-~/informatica-mcp-exports}"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <TICKET> <package.zip> [<package.zip> ...]" >&2
  exit 2
fi

TICKET="$1"; shift
if [[ ! "$TICKET" =~ ^[A-Za-z][A-Za-z0-9]+-[0-9]+$ ]]; then
  echo "Ticket '$TICKET' doesn't look like a Jira key (e.g. DP-4100)." >&2
  exit 2
fi

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$REPO_DIR/runs/$TICKET"
mkdir -p "$DEST"

attach_paths=()
for pkg in "$@"; do
  # Only bare file names: never let a path escape the export directory.
  if [[ "$pkg" == */* || "$pkg" != *.zip ]]; then
    echo "Pass the package file name only (e.g. dev_..._<jobid>.zip), got: $pkg" >&2
    exit 2
  fi
  manifest="${pkg%.zip}.manifest.json"

  echo "Copying $pkg (+ manifest) from $BOX ..."
  scp -q "$BOX:$REMOTE_DIR/$pkg" "$BOX:$REMOTE_DIR/$manifest" "$DEST/"

  expected="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$DEST/$manifest")"
  actual="$(shasum -a 256 "$DEST/$pkg" | awk '{print $1}')"
  if [[ "$expected" != "$actual" ]]; then
    echo "CHECKSUM MISMATCH for $pkg: manifest $expected, file $actual. Not attaching." >&2
    rm -f "$DEST/$pkg"
    exit 1
  fi
  echo "  checksum OK ($actual)"
  attach_paths+=("$DEST/$pkg")
done

echo
echo "Ready to attach (files in $DEST):"
for p in "${attach_paths[@]}"; do echo "  $p"; done
