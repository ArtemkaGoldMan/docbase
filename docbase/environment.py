"""The base sets up what it needs to run, the first time it runs.

A person who has Python and nothing else should not have to install
libraries. With ``"auto_install": true`` in the config, a command that needs
them and cannot find them creates ``.venv`` in the base folder, installs them
there — once — and runs itself again inside it. The hook calls that Python
directly afterwards, so later messages pay nothing for it.

Off by default: a tool installed with pip has its libraries already, and
fetching packages unasked is not something to do to a stranger's machine.
"""
from __future__ import annotations

import os
import subprocess
import sys

from .messages import say

PACKAGES = ("pypdf>=4", "pdfplumber>=0.11", "beautifulsoup4>=4.12")

#: Set in the environment of the second run, so that a setup that did not
#: take cannot start a third.
INSIDE = "DOCBASE_OWN_ENVIRONMENT"


def own_python(root):
    """The interpreter of the base's own environment."""
    folder = os.path.join(root, ".venv")
    if os.name == "nt":
        return os.path.join(folder, "Scripts", "python.exe")
    return os.path.join(folder, "bin", "python")


def _missing():
    from .importers import missing_dependencies
    return missing_dependencies()


def _tail(error):
    """The last line of what a failed setup step said, or its own name."""
    output = (getattr(error, "stderr", b"") or b"").decode("utf-8", "replace").strip()
    return output.splitlines()[-1][:160] if output else str(error)[:160]


def ensure(cfg, command):
    """-> None to carry on in this process, or the exit code of ``command``
    run again in the base's own environment.

    ``command`` is what to run there: the arguments after the interpreter.
    """
    if not getattr(cfg, "auto_install", False) or os.environ.get(INSIDE):
        return None
    root = cfg.layout.root
    from .config import CONFIG_NAME
    if not os.path.isfile(os.path.join(root, CONFIG_NAME)):
        return None                   # not a base: nothing to set up here
    if not _missing():
        return None
    python = own_python(root)
    try:
        if not os.path.isfile(python):
            print(say("Setting up the base's own Python environment — once, it "
                      "takes a minute or two…"), flush=True)
            subprocess.run([sys.executable, "-m", "venv", os.path.join(root, ".venv")],
                           check=True, capture_output=True)
        ready = subprocess.run([python, "-c", "import pypdf, pdfplumber, bs4"],
                               capture_output=True)
        if ready.returncode:
            subprocess.run([python, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", *PACKAGES],
                           check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        print(say("Could not set up the environment: {why}\nCheck the internet "
                  "connection; the next message will try again.", why=_tail(error)))
        return 0
    package = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ, **{INSIDE: "1"})
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in (package, os.environ.get("PYTHONPATH", "")) if p)
    return subprocess.run([python, *command], env=env).returncode
