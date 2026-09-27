#!/bin/sh
# Build the two bases this benchmark runs against, from public documentation.
#   sh benchmarks/human-worded/fetch.sh <folder>
set -e
DEST="${1:-bench-bases}"
mkdir -p "$DEST/rust-book" "$DEST/python-docs"

curl -sL -o "$DEST/rust.tar.gz" https://codeload.github.com/rust-lang/book/tar.gz/refs/heads/main
tar xzf "$DEST/rust.tar.gz" -C "$DEST" book-main/src
(cd "$DEST/book-main" && zip -qr "../rust-book/rust-book.zip" src)
rm -rf "$DEST/book-main" "$DEST/rust.tar.gz"

curl -sL -o "$DEST/python-docs/python-docs.zip" \
  https://docs.python.org/3/archives/python-3.14-docs-text.zip

for base in rust-book python-docs; do
  echo '{"language": "en"}' > "$DEST/$base/docbase.json"
  python -m docbase --root "$DEST/$base" sync
done
