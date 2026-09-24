"""Check the skills an agent wrote for this base.

`docs-tailor` generates skills, and until now nothing looked at them again.
That is the worst place for a silent failure: a skill with broken frontmatter
does not announce itself, it simply never loads, and the base looks tailored
while behaving exactly as it did before.

Only mechanical faults are reported — the ones that stop a skill loading or
point it at something that is no longer there. Whether a skill is any good is
not a thing a checker can tell.
"""
from __future__ import annotations

import os
import re

RE_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)
RE_FIELD = re.compile(r"^(\w+):\s*(.*)$", re.M)

#: The body loads on every trigger, so its length is charged to every
#: conversation that touches it.
BODY_LIMIT = 150

#: A path a skill tells the agent to read. Only ones below a layout folder:
#: `kb/cards/` is the base describing itself, `kb/cards/refunds/` is a claim
#: that a particular card is there.
RE_BASE_PATH = re.compile(r"`?(kb/[\w.-]+/[\w./-]+)`?")


def _fields(head):
    return {m.group(1).lower(): m.group(2).strip()
            for m in RE_FIELD.finditer(head)}


def check_skill(path, root, check_paths=True):
    """-> [(skill name, what is wrong)] for one SKILL.md."""
    folder = os.path.basename(os.path.dirname(path))
    try:
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as error:
        return [(folder, f"cannot be read: {error}")]

    found = []
    head = RE_FRONTMATTER.match(raw)
    if not head:
        return [(folder, "no frontmatter block, so it will never load")]

    fields = _fields(head.group(1))
    name = fields.get("name", "")
    if not name:
        found.append((folder, "no name in its frontmatter"))
    elif name != folder:
        found.append((folder, f'name is "{name}" but the folder is "{folder}"'))
    if not fields.get("description"):
        found.append((folder, "no description, so nothing will ever trigger it"))

    body = raw[head.end():]
    lines = len(body.splitlines())
    if lines > BODY_LIMIT:
        found.append((folder, f"{lines} lines of body; it loads on every "
                              f"trigger, so keep it under {BODY_LIMIT}"))

    if check_paths:
        for reference in sorted(set(RE_BASE_PATH.findall(body))):
            if "<" in reference or ">" in reference:
                continue                 # a placeholder, not a path
            target = os.path.join(root, *reference.rstrip("/").split("/"))
            if not os.path.exists(target):
                found.append((folder,
                              f"points at {reference}, which is not there"))
    return found


def find_skills(root):
    folder = os.path.join(root, ".claude", "skills")
    if not os.path.isdir(folder):
        return []
    out = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name, "SKILL.md")
        if os.path.isfile(path):
            out.append(path)
    return out


def report(root, text_dir="kb/text"):
    """-> (how many skills, [(name, complaint)]).

    Paths are only checked once something has been imported. On an empty base
    every path a skill names is missing, which says nothing about the skill —
    and a check that fires when nothing is wrong is a check people learn to
    scroll past.
    """
    populated = bool(os.path.isdir(os.path.join(root, *text_dir.split("/")))
                     and os.listdir(os.path.join(root, *text_dir.split("/"))))
    skills = find_skills(root)
    problems = []
    for path in skills:
        problems.extend(check_skill(path, root, check_paths=populated))
    return len(skills), problems
