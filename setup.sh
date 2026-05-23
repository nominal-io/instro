#!/bin/bash
set -e

echo "🔧 Setting up development environment..."
echo ""

# --- Git Hooks ---
echo "📌 Configuring git hooks..."
git config core.hooksPath .githooks
echo "   ✅ Git hooks path set to .githooks"
echo ""

echo "✅ Setup complete!"