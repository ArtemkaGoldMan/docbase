"""What the tool says to a person, in the language of the base.

Messages are written in English where they are used, and looked up here by
that English text:

    say("Base: {count} [[count:document|documents]]", count=3)

A base whose config sets ``"interface"`` to a language with a catalog below
speaks that language. Anything a catalog lacks stays in English rather than
failing, so a message added to the code is never lost for want of a
translation — only untranslated.

``[[name:one|few|many]]`` picks the form the number ``name`` needs. English
uses the first two; Ukrainian all three — 1 документ, 2 документи,
5 документів.
"""
from __future__ import annotations

import re

_state = {"language": "en"}

RE_FORMS = re.compile(r"\[\[(\w+):([^\]]*)\]\]")


def use(language):
    """Speak ``language`` from now on, or English if there is no catalog."""
    _state["language"] = language if language in CATALOGS else "en"


def language():
    return _state["language"]


def plural(language, number, forms):
    """The form of a word that goes with ``number``."""
    n = abs(int(number))
    if language == "uk" and len(forms) >= 3:
        if n % 10 == 1 and n % 100 != 11:
            return forms[0]
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return forms[1]
        return forms[2]
    return forms[0] if n == 1 else forms[1] if len(forms) > 1 else forms[0]


def every(message):
    """``message`` in every language there is, English first — for reading
    back what the tool itself once wrote, in whichever language it wrote it."""
    return [message] + [catalog[message] for catalog in CATALOGS.values()
                        if message in catalog]


def say(message, **values):
    """``message`` in the current language, with ``values`` filled in."""
    current = _state["language"]
    translated = CATALOGS.get(current, {}).get(message)
    text, rules = (translated, current) if translated else (message, "en")
    text = RE_FORMS.sub(
        lambda m: plural(rules, values[m.group(1)], m.group(2).split("|")), text)
    return text.format(**values) if values else text


UK = {
    # -- written into converted documents -----------------------------------
    "## Links found on this page": "## Посилання зі сторінки",
    "## Links found in this document": "## Посилання з документа",
    "### Internal": "### Внутрішні",
    "### External": "### Зовнішні",
    "| page | id | URL |": "| сторінка | id | URL |",
    "![image from page {page}]({path})": "![зображення зі сторінки {page}]({path})",
    "<!-- This image carries content that is not in the text. Open the file "
    "only if the answer is not nearby. -->":
        "<!-- Вмісту цього зображення в тексті немає. Відкривай файл, лише якщо "
        "відповіді немає поруч. -->",
    "image from this page": "зображення з цієї сторінки",

    # -- sync -------------------------------------------------------------
    "{name} -> {target}": "«{name}» → {target}",
    " (replaces the previous export)": " (замінює попередній експорт)",
    "{count} duplicate copies removed": "прибрано дублікатів: {count}",
    "{name} is already in the base; removed the copy":
        "«{name}» вже є в базі — копію прибрано",
    "could not open {name}: {why}": "не вдалося відкрити «{name}»: {why}",
    "unpacked {name}: {count} [[count:document|documents]]":
        "розпаковано «{name}»: {count} [[count:документ|документи|документів]]",
    " and {count} [[count:attachment|attachments]]":
        " і {count} [[count:вкладення|вкладення|вкладень]]",
    "{name} held nothing importable": "в «{name}» немає нічого, що можна імпортувати",
    "… and {count} more": "… і ще {count}",
    "{size} KB in, almost no text out — a login or error page, a scan without "
    "a text layer, or an unreadable format":
        "{size} КБ на вході, а тексту майже немає — це сторінка входу чи "
        "помилки, скан без текстового шару або формат, який не читається",
    "{name} changed: +{added}/-{removed} (diff in {folder})":
        "«{name}» змінився: +{added}/-{removed} (різниця — {folder})",
    "card {card} re-bound to {source}": "картку {card} прив'язано до {source}",
    "cards for '{topic}' marked stale": "картки «{topic}» позначено застарілими",
    "The source document changed. Rebuild this card.\n":
        "Документ-джерело змінився. Онови картку.\n",
    "{name} skipped: this page is already available as HTML":
        "«{name}» пропущено: ця сторінка вже є в HTML",
    "{name} is a copy of {other}; skipped": "«{name}» — копія «{other}», пропущено",
    "{why} (damaged, or a format this cannot read)":
        "{why} (файл пошкоджений або це формат, який не читається)",
    "{name}: {share} of the text came out run together — the file does not "
    "write its spaces, so phrases in it will not be found":
        "«{name}»: {share} тексту злиплося без пробілів — у файлі немає "
        "пробілів між словами, тож фрази з нього не знайдуться",
    "added {path}": "додано {path}",
    "{count} [[count:document|documents]] re-read and unchanged":
        "{count} [[count:документ|документи|документів]] перечитано — без змін",
    "  ! could not read '{name}': {why}": "  ! не вдалося прочитати «{name}»: {why}",
    "The base is empty. Export a page from your wiki (PDF, Word or HTML) and "
    "drop the file into this folder.":
        "База порожня. Експортуй сторінку з Confluence (PDF, Word або HTML) і "
        "кинь файл у цю папку.",
    "Base: {count} [[count:document|documents]]":
        "База: {count} [[count:документ|документи|документів]]",
    ", {count} unchanged": ", без змін: {count}",
    "  - linked {count} cross-document [[count:reference|references]]":
        "  - зшито посилань між документами: {count}",
    "  - {count} referenced [[count:page is|pages are]] not imported yet "
    "(see kb/graph.md)":
        "  - бракує {count} [[count:документа|документів|документів]], на які є "
        "посилання (список — kb/graph.md)",
    "Missing Python packages: {names}\nInstall them with:\n  python -m pip install {packages}":
        "Бракує пакетів Python: {names}\nВстанови їх так:\n  python -m pip install {packages}",
    "There is no base here: no {config} in this folder or above it. Create "
    "one with: docbase init":
        "Тут немає бази: ні в цій папці, ні вище немає {config}. Створи її: "
        "python -m docbase init --language uk",
    "The base was not updated: {why}\nRun `python -m docbase sync` to see what "
    "went wrong.":
        "База не оновилась: {why}\nЗапусти `python -m docbase sync`, щоб побачити, "
        "що саме пішло не так.",

    # -- the map of the base (kb/graph.md) --------------------------------
    "# Base map": "# Мапа бази",
    "Generated by `docbase sync`. Shows which documents exist and what":
        "Створюється командою `python -m docbase sync`. Показує, які документи є",
    "they reference.": "і на що вони посилаються.",
    "## Imported": "## Імпортовано",
    "| Document | File | id |": "| Документ | Файл | id |",
    "## Referenced but not imported yet": "## На них посилаються, але їх ще немає",
    "{count} references in total; the {shown} most cited are listed here, and "
    "the rest are in `linkmap.json`.":
        "Усього посилань: {count}; тут {shown} найчастіших, решта — у `linkmap.json`.",
    "| identifier | Referred to as |": "| ідентифікатор | Як названо в посиланні |",
    "Everything referenced is already available locally.":
        "Усе, на що є посилання, вже є в базі.",
    "## Reference graph": "## Граф посилань",

    # -- find and map -------------------------------------------------------
    "The base is empty. Drop an exported page into this folder and run: "
    "docbase sync":
        "База порожня. Кинь експортовану сторінку в цю папку й запусти: "
        "python -m docbase sync",
    "LOW CONFIDENCE — nothing in the base matched the question.":
        "НИЗЬКА ВПЕВНЕНІСТЬ — у базі нічого не збіглося з питанням.",
    "Words the documentation never uses: {words}":
        "Слова, яких у документації немає: {words}",
    "Ask again in the documentation's words, taken from the map above. Only "
    "after that conclude the base does not cover it.":
        "Спитай ще раз словами документації — з мапи вище. Лише після цього "
        "роби висновок, що в базі цього немає.",
    "(no heading)": "(без заголовка)",
    "(truncated — narrow the query)": "(далі обрізано — уточни запит)",
    "The base is empty.": "База порожня.",
    "No document matches {wanted}. Ask for the map without a name to see them all.":
        "Немає документа, що відповідає «{wanted}». Попроси мапу без назви, "
        "щоб побачити всі.",
    "        … {count} more sections": "        … і ще розділів: {count}",
    "… and {count} more [[count:document|documents]]":
        "… і ще {count} [[count:документ|документи|документів]]",
    "\n{count} [[count:document|documents]]. Name one to see its sections.":
        "\n{count} [[count:документ|документи|документів]]. Назви один, щоб "
        "побачити його розділи.",
    "No document called {name}. List the documents for their names.":
        "Немає документа {name}. Назви документів — у python -m docbase map.",
    "(past the end of the document)": "(далі документ закінчується)",

    # -- doubting an answer -------------------------------------------------
    "nothing in the base matched the question": "у базі нічого не збіглося з питанням",
    "the best match is in {name}, whose title shares no word with the "
    "question, though other titles do":
        "найкращий збіг — у {name}, а в його назві немає жодного слова з "
        "питання, хоча в назвах інших документів є",
    "the best match covers little of the question (score {score})":
        "найкращий збіг покриває мало з питання (бал {score})",
    "the best match covers little of the question (score {score}) and "
    "nothing near it agrees":
        "найкращий збіг покриває мало з питання (бал {score}), і поруч немає "
        "нічого, що б його підтвердило",
    "{name} is almost as likely ({second} against {top}) — the question does "
    "not tell the two apart":
        "{name} майже так само ймовірний ({second} проти {top}) — з питання не "
        "зрозуміло, котрий із двох",
    "LOW CONFIDENCE — the best match may be the wrong one:":
        "НИЗЬКА ВПЕВНЕНІСТЬ — найкращий збіг може бути не тим:",
    "Words from the question the documentation never uses: {words}":
        "Слова з питання, яких у документації немає: {words}",
    "  Replace these first — nothing in the base can match them.":
        "  Заміни їх насамперед — у базі з ними нічого не збіжиться.",
    "What the documentation calls the nearby topics:":
        "Як документація називає близькі теми:",
    "Ask again in the documentation's words, taken from the headings above. "
    "If two searches agree on a section, read it whole. If they do not, the "
    "base may not cover this — say so, or ask which of the nearby topics was "
    "meant.":
        "Спитай ще раз словами документації — із заголовків вище. Якщо два "
        "пошуки вказують на той самий розділ, прочитай його повністю. Якщо ні — "
        "можливо, в базі цього немає: так і скажи або спитай, котру з близьких "
        "тем мали на увазі.",

    # -- status -------------------------------------------------------------
    "Documents: {count}": "Документів: {count}",
    "updated {date}": "оновлено {date}",
    "  … and {count} more (docbase map lists them all)":
        "  … і ще {count} (усі — у python -m docbase map)",
    "\nFiles that could not be read:": "\nФайли, які не вдалося прочитати:",
    "\nCards waiting to be rebuilt (their source changed):":
        "\nКартки, які треба оновити (їхнє джерело змінилося):",
    "\nRecent document changes:": "\nОстанні зміни в документах:",
    "\nReferenced but not imported: {count} [[count:page|pages]]":
        "\nНа них є посилання, але їх не імпортовано: {count}",
    "  ... and {count} more (see kb/graph.md)": "  … і ще {count} (див. kb/graph.md)",
    "  Most cited first — those are the ones worth importing next.":
        "  Спершу ті, на які посилаються найчастіше — їх варто додати першими.",
    "\nEverything is current.": "\nУсе актуальне.",

    # -- verify -------------------------------------------------------------
    "No cards to verify. They are written by an agent as it works;":
        "Карток для перевірки немає. Їх пише агент під час роботи;",
    "see the docs-tailor skill.": "див. скіли в .claude/skills.",
    "Cards checked: {cards}   claims confirmed: {claims}":
        "Перевірено карток: {cards}   підтверджено тверджень: {claims}",
    "\nStale markers cleared (claims still hold against the new source):":
        "\nМаркери застарілості знято (твердження досі збігаються з новим джерелом):",
    "\nCards whose source is missing:": "\nКартки, чийого джерела немає:",
    "  The document was removed or never imported; the card cannot":
        "  Документ видалено або ніколи не імпортовано; картці не можна",
    "  be trusted until it is restored.": "  довіряти, доки його не повернуть.",
    "\nCards marked stale (their source changed since):":
        "\nЗастарілі картки (джерело відтоді змінилося):",
    "\nClaims not found in the source:": "\nТвердження, яких немає в джерелі:",
    "\n  {card}  (against {source})": "\n  {card}  (звірено з {source})",
    "    … and {count} more (-v to see all)": "    … і ще {count} (усі — з -v)",
    "\nA number or quotation that is not in the document is either a":
        "\nЧисло чи цитата, яких немає в документі, — це або помилка",
    "transcription error or drift. Fix the card against its source.":
        "переписування, або документ змінився. Виправ картку за джерелом.",
    "\nEvery number and quotation is present in its source.":
        "\nКожне число й цитата є у своєму джерелі.",
    "\nIn other documents of the base — add them to the card's sources:":
        "\nЄ в інших документах бази — додай їх у джерела картки:",
    "number": "число",
    "quote": "цитата",

    # -- eval ---------------------------------------------------------------
    "The base is empty; nothing to generate cases from.":
        "База порожня — немає з чого будувати кейси.",
    "Could not build any cases from {count} fragments.":
        "Не вдалося побудувати жодного кейсу з {count} фрагментів.",
    "Every candidate was rejected: too short, too few distinctive words, or no "
    "phrase that avoids the query terms.":
        "Усі кандидати відкинуто: закороткі, замало характерних слів або немає "
        "фрази, що не повторює слова запиту.",
    "The corpus may be very repetitive, or mostly tables. Write a few cases by "
    "hand in {path} instead.":
        "Можливо, текст дуже одноманітний або це здебільшого таблиці. Напиши "
        "кілька кейсів вручну в {path}.",
    "Wrote {count} generated cases ({kept} hand-written kept) to {path}":
        "Записано {count} згенерованих кейсів (ручних збережено: {kept}) у {path}",
    "Asked for {asked}; the corpus yielded {count} usable ones.":
        "Просили {asked}; придатних вийшло {count}.",
    "Review them: a good case reads like a real question, not keywords.":
        "Переглянь їх: добрий кейс звучить як справжнє питання, а не набір слів.",
    "No cases yet. Build some with: docbase eval --generate":
        "Кейсів ще немає. Побудуй їх: python -m docbase eval --generate",
    "  skipped   marker not in {name}": "  пропуск   маркера немає в {name}",
    "No usable cases: markers no longer match the corpus. Regenerate with: "
    "docbase eval --generate":
        "Придатних кейсів немає: маркери більше не збігаються з текстом. "
        "Перебудуй: python -m docbase eval --generate",
    "\nCases: {count}": "\nКейсів: {count}",
    " ({count} skipped)": " (пропущено {count})",
    "  in returned {limit}   {value}   <- what the agent actually sees":
        "  у видачі з {limit} {value}   <- те, що справді бачить агент",
    "  right document #1  {value}   <- guards against confident wrong answers":
        "  правильний документ №1  {value}   <- захист від упевнених помилок",
    "  search time        {average} ms average, {worst} ms worst":
        "  час пошуку         у середньому {average} мс, найдовше {worst} мс",
    "  context returned   ~{tokens} tokens per query":
        "  обсяг видачі       ~{tokens} токенів на запит",

    # -- selftest -----------------------------------------------------------
    "{count} unexpanded [[count:macro|macros]] ($body) — the export itself lost them":
        "{count} [[count:нерозгорнутий макрос|нерозгорнуті макроси|нерозгорнутих "
        "макросів]] ($body) — їх втратив сам експорт",
    "document": "документ",
    "templates": "шаблони",
    "anchors": "якорі",
    "questions": "питань",
    "{count} ok": "{count} ok",
    "LOST {lost}/{count}": "ВТРАЧЕНО {lost}/{count}",
    "MISSING {lost}/{count}": "НЕМА {lost}/{count}",
    "   lost: «{text}»": "   втрачено: «{text}»",
    "   anchor missing: «{fact}»{why}": "   якір відсутній: «{fact}»{why}",
    "\nWithout a single test ({count}): {names}": "\nБез жодного тесту ({count}): {names}",
    "\nNo cases in {folder} yet. Add some: see docbase/selftest.py for the format.":
        "\nКейсів у {folder} ще немає. Формат описано на початку docbase/selftest.py.",
    "Questions: {total} — search finds {search}, cards cover {card}, missing "
    "{missing}, dangerous {dangerous}":
        "Питання: {total} — пошук знаходить {search}, картки закривають {card}, "
        "нема {missing}, небезпечних {dangerous}",
    "Not in the base: {total} — doubted honestly {honest}, answered anyway "
    "{confident}":
        "Чого в базі немає: {total} — чесно засумнівався {honest}, усе одно "
        "відповів {confident}",
    "search": "пошук",
    "card": "картка",
    "missing": "нема",
    "dangerous": "небезпечно",
    "honest": "чесний сумнів",
    "confident": "відповів",
    "broken": "зламаний файл",
    "DANGEROUS": "НЕБЕЗПЕЧНО",
    "doubted": "сумнів",
    "ANSWERED": "ВІДПОВІВ",
    "BROKEN FILE": "ЗЛАМАНИЙ ФАЙЛ",
    "              looked for «{answer}»": "              шукала «{answer}»",
    "  {question}: was {was}, now {now}": "  {question}: було «{was}», стало «{now}»",
    "  {document}: lost templates {was} → {now}":
        "  {document}: втрачених шаблонів {was} → {now}",
    "Since the last run:": "Зміни від минулого прогону:",
    "All clear.": "Усе чисто.",
    "Problems: {count}": "Проблем: {count}",

    # -- doctor and init ----------------------------------------------------
    "installed": "встановлено",
    "MISSING — run: pip install pypdf pdfplumber beautifulsoup4":
        "НЕМАЄ — встанови: python -m pip install pypdf pdfplumber beautifulsoup4",
    "{count} entries": "записів: {count}",
    "{count} written for this base": "написано для цієї бази: {count}",
    "Created {path}": "Створено {path}",
    "Drop an exported page into this folder and run: docbase sync":
        "Кинь експортовану сторінку в цю папку й запусти: python -m docbase sync",
}

CATALOGS = {"uk": UK}
