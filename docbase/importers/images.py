"""What an extracted picture is, judged from its own bytes.

Shared by every importer that pulls images out of a document, because they all
face the same two questions: what format is this, and is it a picture of
something or a piece of the page's furniture.
"""
from __future__ import annotations

#: What the bytes say they are. A document carries whatever its author
#: embedded, and calling everything that is not a PNG a .jpg wrote two JPEG
#: 2000 images out of a published standard under a name that nothing will
#: open — least of all the agent told the marker is worth opening.
SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x00\x00\x00\x0cjP  ", ".jp2"),
    (b"\xff\x4f\xff\x51", ".j2k"),
    (b"GIF8", ".gif"),
    (b"II*\x00", ".tiff"),
    (b"MM\x00*", ".tiff"),
    (b"BM", ".bmp"),
)

#: A figure is roughly rectangular. A table of contents renders its dotted
#: leader lines as images 1901 pixels wide and 42 tall; they are far too big
#: for any size threshold to catch and they are not pictures of anything.
MAX_ASPECT = 8

#: Markers that carry a JPEG's dimensions, and the ones that carry nothing.
_JPEG_SIZE_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF})
_JPEG_STANDALONE = frozenset({0xD8, 0xD9, 0x01} | set(range(0xD0, 0xD8)))


def extension(data):
    """-> the extension these bytes deserve, or "" if nothing recognises them."""
    for signature, suffix in SIGNATURES:
        if data.startswith(signature):
            return suffix
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def shape(data):
    """-> (width, height) read out of the header, or None if it does not say."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        return (int.from_bytes(data[16:20], "big"),
                int.from_bytes(data[20:24], "big"))
    if data[:2] != b"\xff\xd8":
        return None

    position = 2
    while position + 9 < len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in _JPEG_SIZE_MARKERS:
            return (int.from_bytes(data[position + 7:position + 9], "big"),
                    int.from_bytes(data[position + 5:position + 7], "big"))
        if marker == 0xFF:
            position += 1                      # fill byte
            continue
        if marker in _JPEG_STANDALONE:
            position += 2
            continue
        length = int.from_bytes(data[position + 2:position + 4], "big")
        if length < 2:
            return None
        position += 2 + length
    return None


def is_a_figure(data):
    """Is this a picture of something, or part of the page's furniture?"""
    measured = shape(data)
    if not measured:
        return True                  # it does not say; judge it by size alone
    width, height = measured
    if not width or not height:
        return False
    return max(width, height) / min(width, height) <= MAX_ASPECT
