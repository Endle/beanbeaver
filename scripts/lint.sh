#!/usr/bin/env bash
# Code quality checks for the beancount project
# Run this before committing to catch issues early

set -e  # Exit on first error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "Running code quality checks..."
echo "========================================"

# Track if any check fails
FAILED=0

TOTAL=3

# 1. Ruff linting
echo -e "\n${YELLOW}[1/$TOTAL] Ruff linting...${NC}"
if ruff check --config pyproject.toml .; then
    echo -e "${GREEN}✓ Ruff linting passed${NC}"
else
    echo -e "${RED}✗ Ruff linting failed${NC}"
    FAILED=1
fi

# 2. Ruff formatting check
echo -e "\n${YELLOW}[2/$TOTAL] Ruff format check...${NC}"
if ruff format --check --config pyproject.toml .; then
    echo -e "${GREEN}✓ Ruff format check passed${NC}"
else
    echo -e "${RED}✗ Ruff format check failed${NC}"
    echo "  Run 'ruff format .' to fix"
    FAILED=1
fi

# 3. Mypy type checking
echo -e "\n${YELLOW}[3/$TOTAL] Mypy type checking...${NC}"
if mypy --config-file pyproject.toml . --ignore-missing-imports --no-error-summary 2>/dev/null; then
    echo -e "${GREEN}✓ Mypy type checking passed${NC}"
else
    echo -e "${RED}✗ Mypy type checking found issues${NC}"
    FAILED=1
fi

# Summary
echo -e "\n========================================"
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All $TOTAL checks passed!${NC}"
    exit 0
else
    echo -e "${RED}Some checks failed. Please fix the issues above.${NC}"
    exit 1
fi
