# 0005. Pinned requirements.txt for the image; uv header stays for dev

Date: 2026-10-14 · Status: accepted (issue #1)

## Context

`patrol.py` is a PEP 723 uv script — `uv run patrol.py` resolves dependencies at
run time, which is ideal for dev but wrong for a container: the in-cluster image
(#1) must not resolve or install anything at start (no network package
management during crash loops, no runtime drift). Meanwhile `pyproject.toml`
already mirrors the PEP 723 set for the pytest environment, so a pinned image
dependency list would be a third listing that can drift from the other two.

## Decision

The image installs Python dependencies at build time with pip from a checked-in
`requirements.txt` that pins the same top-level package set exactly (`==`). The
PEP 723 header and the `pyproject.toml` mirror are kept unchanged (dev and test
modes keep working), and a unit test enforces the sync: `requirements.txt` must
name exactly the packages in the PEP 723 header and cover `pyproject.toml`'s
`[project]` dependencies.

## Consequences

Three dependency listings exist (header ranges, pyproject ranges, requirements
pins) — the drift-guard test is the enforcement, so a new dependency cannot land
without all three agreeing. Image dependency bumps are edits to
`requirements.txt` (and optionally the range floors). Alternatives deliberately
rejected for PoC simplicity: running uv inside the image, exporting a uv/pip
lockfile with transitive pins, and installing from `pyproject.toml` (no exact
pins).
