"""Offline tests for eli.py. No network: PDF pages and HTML come from fixtures shaped like the
Sejm's own (headers, footnotes at the foot of a page, articles quoted inside the notice).

Run: python3 -m unittest discover -s tests
"""
import argparse
import datetime as dt
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/polish-law-checker/skills/polish-law-checker/scripts/eli.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("eli", SCRIPT)
eli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eli)

# Three pages as pypdf returns them from a consolidated text: the notice quotes an "Art. 3." of an
# amending act, the annex starts on page 2, a footnote sits at the foot of page 2, page 3 opens
# with the running header and an empty line.
PAGES = [
    "DZIENNIK USTAW\nPoz. 795\nOBWIESZCZENIE\n"
    "1. ogłasza się jednolity tekst ustawy, z uwzględnieniem stanu prawnego na dzień 19 maja 2026 r.\n"
    "2. Tekst jednolity nie obejmuje:\n"
    "„Art. 3. Ustawa wchodzi w życie po upływie miesiąca od dnia ogłoszenia.”\n",
    "Dziennik Ustaw – 2 – Poz. 795\n \n"
    "Załącznik do obwieszczenia Marszałka Sejmu\n"
    "USTAWA\n"
    "Art. 1. Ustawa reguluje sprawy przykładowe.\n"
    "Art. 2. (utracił moc)1)\n"
    "Art. 3. § 1. Kto z winy swej wyrządził drugiemu szkodę, obowiązany jest do jej na-\n"
    "prawienia.\n"
    "§ 2. Przepis § 1 stosuje się odpowiednio.\n"
    "TYTUŁ II\n"
    "Przepisy końcowe\n"
    "Art. 647 [1]. § 1.2) Inwestor odpowiada solidarnie z wykonawcą.\n"
    " \n"
    "1) Z dniem 15 lipca 2006 r. na podstawie wyroku Trybunału Konstytucyjnego\n"
    "z dnia 15 marca 2005 r. sygn. akt K 9/04.\n"
    "2) W brzmieniu ustalonym przez art. 1 pkt 1 ustawy z dnia 9 października 2025 r.\n",
    "Dziennik Ustaw – 3 – Poz. 795\n \n"
    "§ 2. Zgłoszenie nie jest wymagane.\n"
    "Art. 648. Przez umowę o roboty budowlane:\n"
    "1) wykonawca zobowiązuje się do oddania obiektu;\n",
    # A page that opens with a numbered point: it is article text, not a footnote block.
    "Dziennik Ustaw – 4 – Poz. 795\n \n"
    "2) inwestor zobowiązuje się do zapłaty wynagrodzenia.\n",
]


def args(**kw):
    return argparse.Namespace(**kw)


class Addresses(unittest.TestCase):
    def test_eli_forms(self):
        for given in ["DU/2026/795", "du/2026/0795", "2026/795", "Dz. U. z 2026 r. poz. 795",
                      "Dz.U. 2026 poz. 795", "(t.j. Dz. U. z 2026 r., poz. 795 z późn. zm.)"]:
            self.assertEqual(eli.parse_eli(given), "DU/2026/795", given)
        self.assertEqual(eli.parse_eli("M.P. z 2024 r. poz. 12"), "MP/2024/12")
        with self.assertRaises(eli.Problem):
            eli.parse_eli("art. 415 k.c.")

    def test_article_forms(self):
        self.assertEqual(eli.parse_article("415"), ("415", None))
        self.assertEqual(eli.parse_article("art. 13b"), ("13b", None))
        for given in ["449^1", "449¹", "449[1]", "449_1", "Art. 449^1."]:
            self.assertEqual(eli.parse_article(given), ("449", "1"), given)
        self.assertEqual(eli.parse_article("647¹"), ("647", "1"))
        self.assertEqual(eli.parse_article("4491"), ("4491", None))  # a lost superscript stays as typed
        with self.assertRaises(eli.Problem):
            eli.parse_article("§ 2")

    def test_dates(self):
        self.assertEqual(eli.polish_date("na dzień 19 maja 2026 r."), dt.date(2026, 5, 19))
        state, before = eli.notice_cutoffs("z uwzględnieniem stanu prawnego na dzień 19 maja 2026 r. oraz")
        self.assertEqual((state, before), (dt.date(2026, 5, 19), None))
        state, before = eli.notice_cutoffs("oraz zmian wynikających z przepisów ogłoszonych przed dniem 5 grudnia 2023 r.")
        self.assertEqual((state, before), (None, dt.date(2023, 12, 5)))


class Pdf(unittest.TestCase):
    def setUp(self):
        self.saved = eli.pdf_pages
        eli.pdf_pages = lambda data: PAGES

    def tearDown(self):
        eli.pdf_pages = self.saved

    def test_page_split(self):
        lines, notes = eli.split_page(PAGES[1])
        self.assertNotIn("Dziennik Ustaw – 2 – Poz. 795", lines)
        self.assertEqual(set(notes), {"1", "2"})
        self.assertTrue(notes["1"].endswith("sygn. akt K 9/04."))
        lines, notes = eli.split_page(PAGES[3])
        self.assertEqual(notes, {})  # an empty line after the header is not a footnote block
        self.assertEqual(lines, ["2) inwestor zobowiązuje się do zapłaty wynagrodzenia."])

    def test_article_whose_points_run_onto_the_next_page(self):
        text, _, pages = eli.pdf_article(b"", "648", None)
        self.assertEqual(text, ["Art. 648. Przez umowę o roboty budowlane:",
                                "1) wykonawca zobowiązuje się do oddania obiektu;",
                                "2) inwestor zobowiązuje się do zapłaty wynagrodzenia."])
        self.assertEqual(pages, [3, 4])

    def test_article_not_taken_from_the_notice(self):
        text, notes, pages = eli.pdf_article(b"", "3", None)
        self.assertEqual(text[0], "Art. 3. § 1. Kto z winy swej wyrządził drugiemu szkodę, obowiązany jest do jej naprawienia.")
        self.assertEqual(text[1], "§ 2. Przepis § 1 stosuje się odpowiednio.")
        self.assertEqual(len(text), 2)  # the TYTUŁ heading before the next article is not part of it
        self.assertEqual(pages, [2])

    def test_article_over_a_page_break_with_footnote(self):
        text, notes, pages = eli.pdf_article(b"", "647", "1")
        self.assertEqual(text[0], "Art. 647[1]. § 1. Inwestor odpowiada solidarnie z wykonawcą.")
        self.assertEqual(text[1], "§ 2. Zgłoszenie nie jest wymagane.")
        self.assertEqual(pages, [2, 3])
        self.assertEqual(notes, [("§ 1: 2)", "W brzmieniu ustalonym przez art. 1 pkt 1 ustawy z dnia 9 października 2025 r.")])

    def test_struck_down_article_keeps_its_footnote(self):
        text, notes, _ = eli.pdf_article(b"", "2", None)
        self.assertEqual(text, ["Art. 2. (utracił moc)"])
        self.assertIn("Trybunału Konstytucyjnego", notes[0][1])

    def test_missing_article_and_lost_superscript(self):
        self.assertIsNone(eli.pdf_article(b"", "6471", None))
        same, lost = eli.nearby(eli.heads_pdf(b""), "6471", None)
        self.assertEqual(lost, ["Art. 647[1]"])

    def test_notice(self):
        notice = " ".join(eli.pdf_notice(b""))
        self.assertIn("stanu prawnego na dzień 19 maja 2026 r.", notice)
        self.assertNotIn("Załącznik", notice)


class Html(unittest.TestCase):
    data = (FIXTURES / "consolidated.html").read_bytes()

    def test_article_with_paragraphs_and_footnote(self):
        lines, notes, uid = eli.html_article(self.data, "7", None)
        self.assertEqual(uid, "arti_7")
        self.assertEqual(lines[0], "Art. 7.")
        self.assertEqual(lines[1], "1. W transakcjach handlowych wierzycielowi przysługują odsetki, jeżeli:")
        self.assertEqual(lines[2], "1) wierzyciel spełnił swoje świadczenie;")
        self.assertEqual(lines[-1], "2. Termin zapłaty nie może przekraczać 60 dni.")
        self.assertEqual(notes, [("1)", "W brzmieniu ustalonym przez art. 2 ustawy przykładowej.")])

    def test_quoted_article_in_notice_is_not_the_act(self):
        lines, _, uid = eli.html_article(self.data, "13", None)
        self.assertEqual(uid, "titl_I-arti_13")
        self.assertIn("Postanowienia umowy", lines[1])

    def test_superscript_article(self):
        lines, _, uid = eli.html_article(self.data, "300", "1")
        self.assertEqual(lines, ["Art. 300[1]. Spółka, o której mowa w art. 300[2], może być utworzona przez jedną osobę."])

    def test_notice_section(self):
        notice = " ".join(eli.html_notice(self.data))
        self.assertIn("ogłoszonych przed dniem 5 grudnia 2023 r.", notice)
        self.assertNotIn("Postanowienia umowy", notice)


class Cites(unittest.TestCase):
    def test_draft(self):
        found = eli.cmd_cites(args(file=str(FIXTURES / "draft.md")))["citations"]
        texts = [(c["kind"], c["text"]) for c in found]
        self.assertIn(("article", "art. 7 ust. 1"), texts)
        self.assertIn(("journal", "Dz. U. z 2022 r. poz. 893"), texts)
        self.assertIn(("article", "art. 481 § 1"), texts)
        self.assertIn(("act", "k.c."), texts)
        self.assertIn(("article", "art. 4491"), texts)
        self.assertIn(("article", "art. 449¹ § 2"), texts)
        self.assertIn(("paragraph", "§ 3 ust. 2"), texts)
        self.assertFalse(any(k == "paragraph" and t.startswith("§ 1") for k, t in texts))  # part of art. 481 § 1


if __name__ == "__main__":
    unittest.main()
