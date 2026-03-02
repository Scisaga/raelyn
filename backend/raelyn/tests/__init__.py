"""Test package for raelyn.

Run with:
  ./.venv/bin/python -m unittest discover -s backend -p "test_*.py"

Live transcript polish check (calls handlers._polish_transcript_via_llm; prints latency):
  ./.venv/bin/python -m unittest backend.raelyn.tests.test_llm_live
"""
