---
name: polish-law-checker
description: Check every Polish-law citation in a document against the official text from the Sejm's ELI API (api.sejm.gov.pl). It finds the citations, confirms the latest consolidated text (tekst jednolity) and the changes after it, quotes each article word for word, and reports what is wrong. Use when someone drafts, reviews or relies on Polish law — a wezwanie do zapłaty, pismo procesowe, umowa, uchwała, opinia, a compliance or tax note — or asks what "art. X" of a Polish act says. Trigger phrases: "sprawdź przepisy", "zweryfikuj cytowania", "czy ten przepis jest aktualny", "tekst jednolity", "Dz. U.", "art. … k.c.", "check the Polish law citations", "what does art. X say".
---

# Polish law checker

Polish acts change often. A consolidated text (tekst jednolity) goes out of date within months, articles get added with superscript numbers (art. 449¹), and some get repealed or struck down by the Constitutional Tribunal. A citation written from memory, or copied from an older document, is wrong more often than it looks. This skill checks each one at the source and shows the evidence.

**One rule above the rest: never state Polish law from memory.** Every act number, consolidated text and article wording in your answer comes from the script's output in this session. If the script can't reach the API or can't find something, say "not verified" for that citation. Never fill the gap yourself.

## The tool

`scripts/eli.py` in this skill's directory. Run it with uv, which installs its three dependencies (pypdf, truststore, certifi) on first use:

```
uv run <skill-dir>/scripts/eli.py <command> ...
```

Without uv: `pip install pypdf truststore certifi`, then `python3 <skill-dir>/scripts/eli.py ...`. Add `--json` before the command for machine-readable output.

| Command | What it gives |
|---|---|
| `cites <file>` | Every citation in a draft (.txt, .md, .docx, .pdf) with its line number: articles, Dz. U. references, act names, § of a contract or regulation. A checklist, not a guarantee. |
| `find "<words from the title>"` | Base acts whose title matches, amending acts left out. Use it to get an act's address (e.g. "podatku od towarów i usług" → DU/2004/535). |
| `act <act>` | The act's status, its latest consolidated text and older ones, the notice word for word (its cut-off date, what it includes, what it leaves out), every change recorded after the cut-off with its date and whether it is in force, and Constitutional Tribunal rulings published after it. |
| `article <act> <number>` | One article, word for word, from the latest consolidated text, with the source URL, the page (for PDF texts) and its footnotes (which act gave it its wording, and since when). Numbers: `415`, `13b`, `449^1` or `449¹`. |
| `text <act> [--grep REGEX]` | The full official text of exactly that entry, or only the lines that match. Use it on an amending act, to see whether it touches the article you're checking. |

`<act>` is an ELI address (`DU/1964/93`) or a citation (`"Dz. U. z 2026 r. poz. 795"`). For `act` and `article`, giving either the act or any of its consolidated texts works; `article` always reads the latest consolidated text and says which one it read.

Common base acts: Kodeks cywilny DU/1964/93, Kodeks postępowania cywilnego DU/1964/296, Kodeks pracy DU/1974/141, Kodeks spółek handlowych DU/2000/1037, Ordynacja podatkowa DU/1997/926, ustawa o VAT DU/2004/535, ustawa o PIT DU/1991/350, ustawa o CIT DU/1992/86, ustawa o przeciwdziałaniu nadmiernym opóźnieniom w transakcjach handlowych DU/2013/403. These addresses never change. Their consolidated texts do, so always run `act`.

## Procedure

1. **List the citations.** Run `cites` on the document, then read the document yourself and add what it missed: an act named only in words ("ustawa o VAT"), a citation split across lines, a reference like "tej ustawy". Write the list down before checking anything.
2. **Identify each act.** Map every citation to its base act. Use `find` when you don't know the address. If an act name is ambiguous, say so rather than guess.
3. **Check the act** with `act`:
   - Is it in force? A repealed act needs a reason to be cited.
   - Does the document cite the latest consolidated text? An older one is out of date; give the current citation.
   - Read the notice: its cut-off date, the amendments it includes, and the provisions it says it does not include.
   - List the changes recorded after the cut-off. Those published after it are certainly not in the text. For each one, run `text <amending act> --grep "art\. <N>"` (and the act's name) to see whether it changes a cited article. Note its date: a change that is not yet in force is not the law today, but may be by the date that matters for the document.
   - Note any Constitutional Tribunal ruling published after the text.
4. **Check each article** with `article`:
   - Does it exist? A "4491" usually means a superscript lost in Word; the script suggests `449^1` when that article exists.
   - Is it in force? "(uchylony)" means repealed, "(utracił moc)" means struck down by the Tribunal; the footnote says since when.
   - Is the rule where the document says it is? The right ustęp, paragraph (§), point (pkt) or letter (lit.)? Documents often cite the right article but the wrong unit.
   - Does the text say what the document claims? Read the whole article, not one sentence. If the document's argument chains two provisions ("art. X makes clauses contrary to art. Y void"), check both ends.
   - Is a quotation word for word? Compare it against the script's text.
   - Do the footnotes show a recent change of wording, after the document's facts?
   - For a PDF text, the script warns that line-end hyphens were joined by machine. When the exact wording matters, say which page to check.
5. **Report**, in the document's language (usually Polish). One row per citation:

   | # | Citation (line) | Checked against | Result | Evidence | Fix |
   |---|---|---|---|---|---|

   Results: **OK**; **outdated text** (cites an older consolidated text); **wrong unit** (the rule is in another ust./§/pkt); **doesn't say that**; **misquoted**; **not in force** (repealed, struck down, or not yet in force on the relevant date); **changed since** (an amendment after the cut-off touches it); **not found**; **not verified** (the script failed: say why).

   Evidence is the quoted operative words with their source (`Dz. U. z 2026 r. poz. 795, art. 415`, plus the page for PDFs). Fix is the corrected citation or wording. Put the ready-to-paste citation of each consolidated text under the table, e.g. `(Dz. U. z 2026 r. poz. 795)`, adding `z późn. zm.` when changes were published after it.

## What it does not cover

- **EU law** (GDPR, eIDAS, MiCA). It isn't in the Sejm API; say so and check EUR-Lex separately.
- **Court rulings** other than Tribunal rulings published in Dziennik Ustaw. A Tribunal ruling that was never published does not appear.
- **Local law** (voivodeship official journals) and internal regulations.
- **Whether the argument wins.** It checks that the law cited exists, is current and says what the document claims. The legal judgment stays with the author.
