# 0029. Host-portable text I/O: UTF-8 and LF are pinned, not inherited

Date: 2026-11-04 · Status: proposed

## Context

Development happens on Linux and Windows. On Windows the suite failed 22 tests
(#77) and the wiki itself was host-dependent: `write_wiki_atomic` used
`Path.write_text(content)` with no encoding, so the file was written cp1252 with
CRLF. The UTF-8 history reader (`wiki_history`) then failed to decode it *inside
a subprocess reader thread*; the exception was swallowed, `stdout` became
`None`, and churn silently degraded to `[None, None]`. The same locale
dependence sat on the read path (`run_patrol`, `--wiki-stats`) and on the
console, where the ` · ` separator became a bare `0xb7` byte and the `🔔`
prefix could raise `UnicodeEncodeError` on a pipe.

The wiki is durable memory and a git-backed audit trail: its bytes must mean the
same thing on every host, and a reader must never inherit the writer's locale.
The container target is Linux, but a wiki authored on Windows and one authored
on Linux are the same artifact under the spec (`what/wiki-memory.md` rules 4c,
4d, 27).

## Decision

Pin the encoding and newline explicitly everywhere text crosses the process
boundary:

- the wiki is **UTF-8, no BOM, LF**, written with `encoding="utf-8"` and
  `newline="\n"` (rule 4c), so the same content produces identical bytes on
  every host;
- every wiki read decodes **UTF-8 explicitly** (rule 4g), never the ambient
  locale; legacy non-UTF-8 bytes are decoded leniently (`errors="replace"`) so
  an upgrade never crashes and the next **model-authored** accepted write re-encodes the file;
- stdout/stderr are reconfigured to **UTF-8/LF** at startup (rule 9b), so the
  console and `--wiki-stats` block are host-independent and emoji never fail;
- test scaffolding follows the same rule: fixtures write UTF-8, subprocess
  environments start from `os.environ` and delete only `PATROL_*`, and fake
  cluster CLIs are host-portable.

The rejected alternatives: inheriting the locale (the status quo — silent
corruption and swallowed decode failures); writing only on Linux (Windows
contributors then cannot trust the suite or a local wiki); a strict UTF-8 read
that fails on a legacy cp1252 wiki (turns an upgrade into a crash loop, against
rule 4b/19's "never block on the wiki" philosophy); and widening the image or
deployment (out of scope — there is no Windows deployment target).

## Consequences

- A wiki written on Windows is byte-identical to one written on Linux; the git
  archive is portable and `wiki_history` reads it.
- A pre-#77 wiki keeps working: it is read with replacement characters and
  becomes UTF-8 on the next model-authored accepted write (the model-free
  minimal-write legs refuse it — `wiki-memory.md` rule 31).
- Tests may decode captured console bytes as UTF-8 instead of guessing the
  host locale; the `run_stats` helper's `PYTHONIOENCODING` workaround is
  unnecessary.
- The dev/test work is test scaffolding, not behavior: fake CLIs and subprocess
  envs are harness concerns (`AGENTS.md`), so the spec's contract surface stays
  the three rules above.
- The dev-Python pin (`.python-version` 3.12, shipped with #77) is the other half
  of "the same command works everywhere"; it is a dev convention, not a
  behavioral contract.
