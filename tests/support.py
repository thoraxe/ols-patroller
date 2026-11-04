"""Host-portable test helpers (#77).

The suite runs on Linux and Windows with the same command, so shared
scaffolding must not assume a POSIX shell, a minimal subprocess environment, or
the ambient locale. See `AGENTS.md` and ADR 0029.
"""
import os
import sys


def patrol_free_env(**extra: str) -> dict[str, str]:
    """The real environment minus `PATROL_*` (rule 23's offline guarantee).

    Starting from `os.environ` rather than a hand-built minimal env keeps the
    host's required variables (notably `SystemRoot` on Windows) intact: a
    minimal env makes the child die on `import asyncio` with `WinError 10106`.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PATROL_")}
    env.update(extra)
    return env


def hostile_locale_env(**extra: str) -> dict[str, str]:
    """An ASCII-locale environment with Python's UTF-8 mode forced off.

    Proves the process pins UTF-8 itself instead of inheriting the locale. On
    Windows `LC_ALL`/`LANG` are ignored and the default ANSI code page applies;
    on POSIX the C locale without PEP 538/540 coercion yields ASCII.
    """
    env = patrol_free_env(**extra)
    for key in ("PYTHONIOENCODING", "PYTHONUTF8", "PYTHONCOERCECLOCALE"):
        env.pop(key, None)
    env.update({"LC_ALL": "C", "LANG": "C",
                "PYTHONCOERCECLOCALE": "0", "PYTHONUTF8": "0"})
    return env


def fake_cli(tmp_path, body: str, name: str = "fake-cli") -> str:
    """A throwaway executable standing in for oc/kubectl in real mode.

    `body` is Python source, not a POSIX shell snippet, so the same fixture
    runs on every host: POSIX gets an executable script with a shebang,
    Windows gets a `.cmd` launcher that runs the same file through the dev
    interpreter. Callers execute it exactly as the guard does — `[cli, *args]`.
    """
    script = tmp_path / f"{name}.py"
    script.write_text(body, encoding="utf-8", newline="\n")
    if os.name == "nt":
        launcher = tmp_path / f"{name}.cmd"
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8", newline="")
        return str(launcher)
    cli = tmp_path / name
    cli.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8", newline="\n")
    cli.chmod(0o755)
    return str(cli)
