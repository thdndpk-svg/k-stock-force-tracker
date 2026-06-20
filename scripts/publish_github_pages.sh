#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: ./scripts/publish_github_pages.sh https://github.com/YOUR_ID/k-stock-force-tracker.git"
  exit 1
fi

REMOTE_URL="$1"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ "$REMOTE_URL" == *"YOUR_ID"* ]]; then
  echo "Replace YOUR_ID with your real GitHub username or organization."
  echo "Example: ./scripts/publish_github_pages.sh https://github.com/myname/k-stock-force-tracker.git"
  exit 1
fi

cd "$PROJECT_DIR"

if [ ! -f ".env.example" ] || [ ! -d "docs" ] || [ ! -f ".github/workflows/mobile-analysis.yml" ]; then
  echo "This does not look like the k-stock-force-tracker project root."
  exit 1
fi

if [ ! -d ".git" ]; then
  git init
fi

git branch -M main

if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REMOTE_URL"
else
  git remote add origin "$REMOTE_URL"
fi

git add .
git commit -m "Deploy mobile stock viewer" || true
git push -u origin main

echo
echo "Uploaded. Now set GitHub Pages Source to GitHub Actions and add repository secrets."
