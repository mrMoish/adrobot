# Adrobot — Evaluation Guide

## Project structure

- Creator: campaign creation
- Editor: flow management
- Keitaro API: shared integration layer

## Verification

Activate the environment described in the README. Run `python -m unittest discover -s tests -v` with the project dependencies alone, or install pytest with `python -m pip install pytest` for the checklist below. The suite runs without credentials.

1. Run `pytest -q`.
2. Check campaign creation with mocked API.
3. Verify GEO filtering and flow priority.
4. Verify offer selection and API error handling.
5. Check that no credentials are exposed.

## Environment limitations

The provided Keitaro account has access to only one offer. Multiple-offer behavior is covered by automated tests.

## Evidence

The editor tests exercise a four-way split (25/25/25/25), pinning and redistribution (25/38/37), and restoring an offer after external deletion. They verify that Fetch retains the archive across repeated reads and that Bring Back publishes the restored association only after Push.

See the [campaign tests](tests/test_campaigns.py), [editor and routing tests](tests/test_sections.py), and implementation files for the [Creator](app/creator.py), [Editor](app/editor.py), [offer catalog](app/offers.py), and [shared Keitaro API client](app/tracker.py).
