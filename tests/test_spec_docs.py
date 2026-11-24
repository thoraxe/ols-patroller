"""Spec-document shape — contract tests for #124.

These tests pin `.ai/spec/README.md`'s Writing rules 11-13, the document-shape
rules added for the readable-docs pass (#124):

    rule 11: every table row at most MAX_DOC_LINE characters
    rule 12: one cell count per table block (escaped pipes only), no wrapped
             table cell, and no unescaped pipe in a table row's code span
    rule 13: at most MAX_DOC_LINE characters per non-fenced line
    the README states all three rules and names this test file

Rules 14-15 (behavior stays in `what/`, no meaning change) are review-checked,
not machine-checked.

The tests fail before the docs cleanup: oversized table rows and broken table
rows (a wrapped cell loses its trailing pipe) are present in `.ai/spec/` and the
top-level docs. They pass after the cleanup, and they stay green as a ratchet on
new violations.

Contract: `.ai/spec/README.md` Writing rules 11-15; ADR 0042.
Run: uv run --group dev pytest tests/ -q
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
SPEC = REPO / ".ai/spec"
MAX_DOC_LINE = 500
TOP_LEVEL_DOCS = ("README.md", "ARCHITECTURE.md", "AGENTS.md")


def spec_docs():
    """Every spec doc plus the top-level docs, in a stable order.

    The gitignored `verification/` review reports are not contract documents.
    """
    docs = [path for path in SPEC.rglob("*.md")
            if "verification" not in path.parts]
    return sorted(docs + [REPO / name for name in TOP_LEVEL_DOCS])


def unfenced_lines(path):
    """Yield (line number, text) for each line outside a fenced code block.

    A line over the cap inside a fenced block is code evidence, not prose.
    """
    in_fence = False
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield number, line


def cell_count(line):
    """The number of table cells in a row.

    A leading and a trailing pipe each add an empty edge cell, so the header
    `| a | b |` has four cells. GitHub Flavored Markdown splits a cell on every
    pipe except an escaped one, so a pipe inside a code span is still a
    separator. Only an escaped pipe (backslash-pipe) is a literal.
    """
    count = 1
    index = 0
    while index < len(line):
        char = line[index]
        if (char == chr(92) and index + 1 < len(line)
                and line[index + 1] == "|"):
            index += 2
            continue
        if char == "|":
            count += 1
        index += 1
    return count


def table_blocks(path):
    """The consecutive pipe-leading line groups of a document, fence-aware."""
    blocks = []
    block = []
    for number, line in unfenced_lines(path):
        if line.lstrip().startswith("|"):
            block.append((number, line))
        elif block:
            blocks.append(block)
            block = []
    if block:
        blocks.append(block)
    return blocks


def test_no_line_over_the_character_cap():
    """Rule 13 (#124): no non-fenced line in `.ai/spec/` or the top-level docs
    is longer than 500 characters. A longer row becomes a short row plus a
    wrapped bulleted list below the table."""
    offenders = []
    for path in spec_docs():
        for number, line in unfenced_lines(path):
            if len(line) > MAX_DOC_LINE:
                offenders.append(
                    f"{path.relative_to(REPO)}:{number} ({len(line)})")
    assert not offenders, (
        f"{len(offenders)} non-fenced line(s) over {MAX_DOC_LINE} characters:\n"
        + "\n".join(offenders))


def test_every_table_block_has_one_cell_count():
    """Rule 12 (#124): every row of a table block has the same cell count. A
    row that wraps inside a cell loses its trailing pipe and breaks the table."""
    offenders = []
    for path in spec_docs():
        for block in table_blocks(path):
            counts = sorted({cell_count(line) for _, line in block})
            if len(counts) > 1:
                offenders.append(
                    f"{path.relative_to(REPO)}:{block[0][0]} cells={counts}")
    assert not offenders, (
        "table blocks with more than one cell count:\n" + "\n".join(offenders))


def test_no_wrapped_table_cell():
    """Rule 12 (#124): a cell stays on one line. A non-blank line directly after
    a table row, with no leading pipe, is a wrapped cell that breaks the table."""
    offenders = []
    for path in spec_docs():
        lines = list(unfenced_lines(path))
        for (number, line), (_next_number, next_line) in zip(lines, lines[1:]):
            if (line.lstrip().startswith("|") and next_line.strip()
                    and not next_line.lstrip().startswith("|")):
                offenders.append(f"{path.relative_to(REPO)}:{number}")
    assert not offenders, (
        "table rows followed by a wrapped cell line:\n" + "\n".join(offenders))


def code_spans(line):
    """The single-backtick code spans of a line, in order."""
    spans = []
    index = 0
    while True:
        start = line.find("`", index)
        if start < 0:
            break
        end = line.find("`", start + 1)
        if end < 0:
            break
        spans.append(line[start + 1:end])
        index = end + 1
    return spans


def test_no_unescaped_pipe_inside_a_code_span():
    """Rule 12 (#124): a literal pipe is escaped. In a table row, a pipe
    inside a code span still splits the cell, so the row must escape it."""
    offenders = []
    for path in spec_docs():
        for number, line in unfenced_lines(path):
            if not line.lstrip().startswith("|"):
                continue
            for span in code_spans(line):
                if re.search(r"(?<!\\)\|", span):
                    offenders.append(f"{path.relative_to(REPO)}:{number}")
                    break
    assert not offenders, (
        "table rows with an unescaped pipe in a code span:\n"
        + "\n".join(offenders))


def test_fence_awareness_exempts_code_blocks(tmp_path):
    """Rule 13 (#124): a fenced block is exempt. A long code line never trips
    the cap, and an unfenced line after the fence still counts."""
    path = tmp_path / "doc.md"
    path.write_text("```\n" + "x" * 600 + "\n```\nshort\n", encoding="utf-8")
    assert [line for _, line in unfenced_lines(path)] == ["short"]


def test_escaped_pipe_is_the_only_literal():
    """Rule 12 (#124): an escaped pipe never separates cells. A pipe inside a
    code span still separates them, as GitHub Flavored Markdown renders it."""
    assert cell_count("| a " + chr(92) + "| b | c |") == 4
    assert cell_count("| `a|b` | c |") == 5


def test_readme_states_the_document_shape_rules():
    """Rules 11–15 (#124) are stated in the README, and the README names the
    test file that pins the machine-checked ones. The pins cover the numeric
    contract and the rule numbers, not editorial wording."""
    text = (SPEC / "README.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    assert "at most 500 characters" in flat
    assert "number of cells" in flat
    assert "tests/test_spec_docs.py" in flat
    numbers = set(re.findall(r"(?m)^(\d+)\. ", text))
    assert {"11", "12", "13", "14", "15"} <= numbers
