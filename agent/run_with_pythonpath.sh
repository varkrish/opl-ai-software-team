#!/usr/bin/env bash
# Quick test runner with PYTHONPATH fix

# Set PYTHONPATH to include src directory
export PYTHONPATH="$(pwd)/src:$PYTHONPATH"

# Run the test command
exec "$@"
