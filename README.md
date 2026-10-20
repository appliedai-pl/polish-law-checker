# polish-law-checker

A Claude Code skill that checks the Polish-law citations in a document against the official text. It reads the Sejm's ELI API (api.sejm.gov.pl), finds the latest consolidated text (tekst jednolity) of each act and the changes made after it, quotes each cited article word for word, and reports what is out of date, misplaced or wrong.

We built it because citations go stale quietly. A consolidated text is replaced within months, articles move or get superscript numbers, and a letter drafted from memory or from last year's template can cite an old Dz. U. entry, the wrong ustęp, or an article the Constitutional Tribunal struck down. Checking by hand means opening each act and each article. The skill does that for every citation and shows the evidence.

## Install

```
/plugin marketplace add appliedai-pl/polish-law-checker
/plugin install polish-law-checker@appliedai
```

It needs [uv](https://docs.astral.sh/uv/), which installs the script's three dependencies on first run. Then ask Claude something like "sprawdź cytowania przepisów w wezwanie.docx" or "what does art. 7 of the late-payments act say now?".

## What it does

The skill gives Claude a procedure and one script, `scripts/eli.py`, that also works on its own:

```
uv run eli.py cites wezwanie.docx           # every citation in the draft, with line numbers
uv run eli.py find "podatku od towarów"     # an act's address: DU/2004/535
uv run eli.py act DU/1964/93                # latest consolidated text, its notice, changes since
uv run eli.py article DU/1964/93 449^1      # one article, word for word, with its footnotes
uv run eli.py text DU/2026/507 --grep "art\. 4"   # does this amending act touch art. 4?
```

From `act DU/1964/93` (the Civil Code) on 20 October 2026, shortened:

```
Latest consolidated text: Dz. U. z 2026 r. poz. 795 (DU/2026/795), notice of 2026-05-27, published 2026-06-17
It gives the law as it stood on 2026-05-19 (from the notice).
Older consolidated texts (a draft that cites one of these is out of date): Dz. U. z 2025 r. poz. 1071, ...

Changes to this act recorded after that cut-off (check each against the cited articles):
  Dz. U. z 2026 r. poz. 507 (DU/2026/507): changes this act from 2028-11-01 (NOT YET IN FORCE); published 2026-04-13
```

And `article DU/1964/93 179`, shortened:

```
This article is NO LONGER LAW in this text: repealed (uchylony), struck down (utracił moc) or left out (pominięty).

Art. 179. (utracił moc)

Footnotes on this article (where its wording comes from):
  2) Z dniem 15 lipca 2006 r. na podstawie wyroku Trybunału Konstytucyjnego z dnia 15 marca 2005 r. sygn. akt K 9/04 (Dz. U. poz. 462).
```

The report Claude writes has one row per citation: what it was checked against, the result (OK, outdated text, wrong unit, doesn't say that, misquoted, not in force, changed since, not found, not verified), the quoted evidence with its source, and the fix.

On the sample letter in `tests/fixtures/draft.md`, a made-up payment demand with planted mistakes, a run with Claude Sonnet on 20 October 2026 reported:

- both consolidated texts out of date (the late-payments act cited as Dz. U. z 2022 r. poz. 893, the Civil Code as Dz. U. z 2025 r. poz. 1071), with the current citations;
- "art. 4491" not found, with a pointer to art. 449¹ (a superscript lost in Word);
- art. 449¹ § 2 cited for producer liability, which is in § 1 (§ 2 defines a product);
- art. 13 ust. 1 cited as making a 90-day payment term void, while the rule for payment terms is in ust. 2, and it substitutes 60 days rather than voiding the clause;
- the contract's own § 3 listed as not verifiable, since it isn't law.

## How it reads the law

- **Metadata:** every act's metadata comes from `api.sejm.gov.pl/eli/acts/DU/<year>/<position>`: status, consolidated texts, amending acts with the date each change takes effect, Tribunal rulings. It is fetched fresh on every run.
- **Texts:** the HTML when the Sejm publishes one, because it is structured into articles, ustępy and points. Otherwise the PDF, read with pypdf: running headers and the footnote block at the foot of each page are separated out, superscripts show as `449[1]`, and articles quoted inside the notice are skipped. Texts are cached in `~/.cache/polish-law-checker`, since a published entry doesn't change.
- **The notice before a consolidated text** says what the text covers: either the law "as it stood on" a date, or changes "from provisions published before" a date, plus what it leaves out. `act` prints it word for word and lists every change recorded after that cut-off.
- **ISAP isn't used** because it blocks scripts. `eli.sejm.gov.pl` isn't used because its certificate doesn't match. The API's certificate chains to Certum EC-384 CA, which some Python builds lack, so the script uses the system trust store and falls back to certifi.

## Limits

- PDF extraction is good, not perfect. Line-end hyphens are joined and the spacing inside a word can be off, so the script says which page to check when the exact wording matters.
- It covers acts in Dziennik Ustaw (and, partly, Monitor Polski). EU law, court rulings other than Tribunal rulings published in Dziennik Ustaw, and local law are out of scope; the skill says so instead of guessing.
- `cites` finds the usual citation forms; unusual ones are missed, so the skill also reads the document itself.
- It checks that the law cited exists, is current and says what the document claims. Whether the argument holds is still the author's call.

## Tests

```
python3 -m unittest discover -s tests
```

Offline, with fixtures shaped like the Sejm's HTML and PDF text: the notice's quoted articles, footnotes at the foot of a page, an article running onto the next page, a struck-down article, a superscript lost in Word.

## Po polsku

Skill do Claude Code, który sprawdza cytowania przepisów w dokumencie (wezwaniu, piśmie, umowie, opinii) u źródła, w API ELI Sejmu. Dla każdej ustawy podaje aktualny tekst jednolity, treść obwieszczenia (stan prawny, czego tekst nie obejmuje) i zmiany ogłoszone po nim. Każdy cytowany artykuł przytacza dosłownie, z przypisami o tym, kto i kiedy nadał mu brzmienie. Na końcu wskazuje, co jest nieaktualne, w złej jednostce redakcyjnej, źle zacytowane albo już nie obowiązuje. Nie zastępuje oceny prawnika: sprawdza, czy przywołany przepis istnieje, obowiązuje i mówi to, co twierdzi dokument.

## Authors

Paweł Rojek and Piotr Rojek, [Applied AI](https://appliedai.dev). Questions and bug reports: GitHub issues, or kontakt@appliedai.dev.

MIT licence.
