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

#: Paths a skill tells the agent to read. One that no longer exists sends the
#: agent looking for a card that was renamed three imports ago.
RE_BASE_PATH = re.compile(r"`?(kb/[\w./-]+)`?")


def _fields(head):
    return {m.group(1).lower(): m.group(2).strip()
            for m in RE_FIELD.finditer(head)}


def check_skill(path, root):
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

    for reference in sorted(set(RE_BASE_PATH.findall(body))):
        target = os.path.join(root, reference.rstrip("/"))
        if not os.path.exists(target):
            found.append((folder, f"points at {reference}, which is not there"))
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


def report(root):
    """-> (how many skills, [(name, complaint)])."""
    skills = find_skills(root)
    problems = []
    for path in skills:
        problems.extend(check_skill(path, root))
    return len(skills), problems
