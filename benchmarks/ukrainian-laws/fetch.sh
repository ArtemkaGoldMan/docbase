#!/bin/sh
# Build the base this benchmark runs against: eight Ukrainian laws on consumer
# financial services, from the parliament's public legislation database.
#   sh benchmarks/ukrainian-laws/fetch.sh <folder>
# The site serves compressed pages and sometimes turns away clients that do
# not look like a browser, hence the flags.
set -e
DEST="${1:-bench-bases}/ukrainian-laws"
mkdir -p "$DEST"
AGENT="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
for law in 1023-12 1591-20 2297-17 1734-19 4452-17 2121-14 393/96-вр 1953-20; do
  file="$DEST/law-$(echo "$law" | tr '/' '_').html"
  curl -sL --compressed -A "$AGENT" -o "$file" "https://zakon.rada.gov.ua/laws/show/$law/print"
  sleep 1
done
echo '{"language": "uk", "importer": {"internal_hosts": ["zakon.rada.gov.ua"]}}' > "$DEST/docbase.json"
python -m docbase --root "$DEST" sync
