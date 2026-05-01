"""Test package for raelyn.

Run with:
  ./.venv/bin/python -m unittest discover -s backend -p "test_*.py"

Conventions:
  - Always use the project's .venv Python instead of system Python.
  - Use unittest as the default test runner.
  - Do not point tests at the active development database; use isolated test state.

Live transcript polish check (calls handlers._polish_transcript_via_llm; prints latency):
  ./.venv/bin/python -m unittest backend.raelyn.tests.test_llm_live
"""
