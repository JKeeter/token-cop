#!/usr/bin/env bash
# Refreshes the kiosk's static image assets:
#   1. copies the dashboard screenshots + GitHub QR from the repo root
#   2. (optional) re-renders the two deck slides to PNG
#
# Usage: bash kiosk/scripts/export-slides.sh [path-to-pptx]
#
# PREFERRED slide refresh — manual PowerPoint export (no extra software):
#   open the deck in PowerPoint → File → Export → PNG (exports every slide
#   to a folder), then copy the cost-crisis-headlines slide to
#   kiosk/public/slides/hook-uber.png and the "Anatomy of Agentic AI Cost"
#   slide to kiosk/public/slides/cost-anatomy.png, and downscale:
#   sips --resampleWidth 1920 kiosk/public/slides/*.png
#   (PowerPoint CANNOT be scripted for this: its sandbox rejects AppleScript
#   save targets with sandbox_extension_issue_file — manual export only.)
#
# This script's automated slide path instead uses LibreOffice headless +
# poppler (brew install --cask libreoffice && brew install poppler) and is
# skipped with a notice when they aren't installed. The copied assets in
# step 1 always refresh regardless.
set -euo pipefail

PPTX="${1:-/Users/jkeeter/projects/presentations/AWS-PE-Tokenomics-Webinar-JMI.pptx}"
KIOSK="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$KIOSK/.." && pwd)"
SOFFICE="${SOFFICE:-/Applications/LibreOffice.app/Contents/MacOS/soffice}"

mkdir -p "$KIOSK/public/dash" "$KIOSK/public/qr" "$KIOSK/public/slides"

echo "== copying repo assets"
cp "$REPO/dashboard-01-overview.png" "$KIOSK/public/dash/overview.png"
# 07 (model efficiency scores) not 02 (per-principal) — 02 is an empty state
cp "$REPO/dashboard-07-per-user-by-model-tab.png" "$KIOSK/public/dash/per-user.png"
cp "$REPO/dashboard-05-recommendations.png" "$KIOSK/public/dash/recommendations.png"
cp "$REPO/token-cop-github-qr.png" "$KIOSK/public/qr/github.png"

if [[ ! -f "$PPTX" ]]; then
  echo "pptx not found: $PPTX — skipping slide export" >&2
  exit 0
fi
if [[ ! -x "$SOFFICE" ]] || ! command -v pdftoppm >/dev/null; then
  echo "LibreOffice/poppler not installed — skipping slide render." >&2
  echo "Use the manual PowerPoint export described in the header instead." >&2
  exit 0
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== rendering deck to PDF (this takes a minute for a 46 MB deck)"
# fresh profile dir avoids "silently produces nothing" after a killed run
"$SOFFICE" -env:UserInstallation="file://$WORK/lo-profile" --headless --convert-to pdf --outdir "$WORK" "$PPTX" >/dev/null
PDF="$WORK/$(basename "${PPTX%.*}").pdf"

render() { # render <page> <output-name>
  pdftoppm -png -r 150 -f "$1" -l "$1" "$PDF" "$WORK/page"
  mv "$WORK"/page-*.png "$KIOSK/public/slides/$2"
  sips --resampleWidth 1920 "$KIOSK/public/slides/$2" >/dev/null
  echo "   slide $1 → public/slides/$2"
}

# NOTE: 32 of the deck's 62 slides are hidden, so the rendered PDF has 30
# pages and PDF page numbers do NOT match pptx slide numbers. Mapping below
# was verified visually (page 2 = cost-crisis headlines hook, page 3 =
# "Anatomy of Agentic AI Cost", page 4 = snowball — rebuilt natively).
echo "== extracting slides"
render 2 hook-uber.png
render 3 cost-anatomy.png
echo "done"
