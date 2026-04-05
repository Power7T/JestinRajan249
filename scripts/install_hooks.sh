#!/usr/bin/env bash
# install_hooks.sh — Install all git hooks for this project.
# Run once after cloning: bash scripts/install_hooks.sh

set -e
HOOKS_DIR=".git/hooks"

if [ ! -d "$HOOKS_DIR" ]; then
    echo "Error: .git not found. Run this from the repo root."
    exit 1
fi

echo "Installing HostAI git hooks..."

# pre-commit
cp scripts/pre-commit "$HOOKS_DIR/pre-commit"
chmod +x "$HOOKS_DIR/pre-commit"
echo "  ✅ pre-commit hook installed"

echo ""
echo "All hooks installed. They will run automatically on 'git commit'."
echo "To run manually: bash scripts/pre-commit"
echo "To skip (EMERGENCY ONLY): git commit --no-verify"
