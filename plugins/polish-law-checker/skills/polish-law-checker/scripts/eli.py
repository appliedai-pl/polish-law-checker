#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pypdf>=5,<7", "truststore>=0.9", "certifi"]
# ///
"""Check Polish-law citations at the source: the Sejm's ELI API (https://api.sejm.gov.pl/eli).

  find "<words from the title>"   base acts whose title matches (amending acts left out)
  act <act>                       the act, its latest consolidated text, what that text leaves out,
                                  and the changes recorded after it
  article <act> <number>          one article word for word, from the latest consolidated text
  text <act> [--grep REGEX]       the whole official text of exactly that entry, or the lines that match
  cites <file>                    every citation in a draft (.txt, .md, .docx, .pdf), with line numbers

<act> is an ELI address (DU/1964/93) or a Dz.U. citation ("Dz. U. z 2026 r. poz. 795").
Run with uv, which installs pypdf on the fly:  uv run eli.py act DU/1964/93
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from html.parser import HTMLParser
from pathlib import Path

VERSION = "1.0.0"
API = "https://api.sejm.gov.pl/eli"
USER_AGENT = f"polish-law-checker/{VERSION} (+https://github.com/appliedai-pl/polish-law-checker)"
TJ_REF = "Inf. o tekście jednolitym"
MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
}
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹", "0123456789")


class Problem(Exception):
    """A failure the user should read, not a traceback."""


# ---------------------------------------------------------------- addresses and numbers

def parse_eli(text: str) -> str:
    """'DU/2026/795', '2026/795', 'Dz. U. z 2026 r. poz. 795', 'Dz.U. 2026 poz. 795' -> 'DU/2026/795'."""
    s = text.strip()
    m = re.fullmatch(r"(?i)(DU|MP)\s*/\s*(\d{4})\s*/\s*(\d+)", s)
    if m:
        return f"{m[1].upper()}/{m[2]}/{int(m[3])}"
    m = re.fullmatch(r"(\d{4})\s*/\s*(\d+)", s)
    if m:
        return f"DU/{m[1]}/{int(m[2])}"
    m = re.search(r"(?i)\b(Dz\.?\s*U|M\.?\s*P)\.?\s*(?:z\s*)?(\d{4})\s*(?:r\.)?\s*,?\s*(?:Nr\s*\d+\s*,?\s*)?poz\.?\s*(\d+)", s)
    if m:
        pub = "MP" if m[1].upper().startswith("M") else "DU"
        return f"{pub}/{m[2]}/{int(m[3])}"
    raise Problem(f"Not an act address: {text!r}. Use DU/<year>/<position>, e.g. DU/1964/93, "
                  "or a citation like 'Dz. U. z 2026 r. poz. 795'.")


def eli_key(eli: str) -> tuple[str, int, int]:
    pub, year, pos = eli.split("/")
    return pub, int(year), int(pos)


def dzu(eli: str) -> str:
    pub, year, pos = eli.split("/")
    return f"{'Dz. U.' if pub == 'DU' else 'M.P.'} z {year} r. poz. {pos}"


def parse_article(text: str) -> tuple[str, str | None]:
    """'449^1', '449¹', '449[1]', '449_1', 'art. 13b' -> ('449', '1'), ('13b', None)."""
    s = re.sub(r"[⁰¹²³⁴⁵⁶⁷⁸⁹]+", lambda m: "^" + m[0].translate(SUPERSCRIPTS), text.strip())
    s = re.sub(r"(?i)^art(ykuł|\.)?\s*", "", s).rstrip(".")
    m = re.fullmatch(r"(\d+[a-z]*)\s*(?:\^|\[|_|\()?\s*(\d+)?\s*[\])]?", s, re.I)
    if not m or (not m[2] and not re.fullmatch(r"\d+[a-z]*", s, re.I)):
        raise Problem(f"Not an article number: {text!r}. Examples: 415, 13b, 449^1, 449¹.")
    return m[1].lower(), m[2]


def article_label(base: str, sup: str | None) -> str:
    return f"Art. {base}{f'[{sup}]' if sup else ''}"


def polish_date(text: str) -> dt.date | None:
    m = re.search(r"(\d{1,2})\s+([a-ząćęłńóśźż]+)\s+(\d{4})", text)
    if m and m[2] in MONTHS:
        return dt.date(int(m[3]), MONTHS[m[2]], int(m[1]))
    return None


# ---------------------------------------------------------------- network and cache

def cache_dir() -> Path:
    base = os.environ.get("POLISH_LAW_CHECKER_CACHE")
    if base:
        return Path(base)
    xdg = os.environ.get("XDG_CACHE_HOME")
    return (Path(xdg) if xdg else Path.home() / ".cache") / "polish-law-checker"


def tls_contexts() -> list[ssl.SSLContext | None]:
    """The system's trust store first (works behind company proxies), then Mozilla's (certifi).
    api.sejm.gov.pl chains to Certum EC-384 CA, a root some Python builds don't carry."""
    out: list[ssl.SSLContext | None] = []
    try:
        import truststore
        out.append(truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
    except ImportError:
        pass
    try:
        import certifi
        out.append(ssl.create_default_context(cafile=certifi.where()))
    except ImportError:
        pass
    return out or [None]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last: Exception | None = None
    for attempt in range(3):
        for ctx in tls_contexts():
            try:
                with urllib.request.urlopen(req, timeout=90, context=ctx) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    raise Problem(f"Not found at the source: {url}") from e
                last = e
                break
            except urllib.error.URLError as e:
                last = e
                if isinstance(e.reason, ssl.SSLCertVerificationError):
                    continue
                break
            except TimeoutError as e:
                last = e
                break
        time.sleep(1.5 * (attempt + 1))
    raise Problem(f"Could not reach {url}: {last}. Nothing was verified.")


def meta(eli: str) -> dict:
    return json.loads(fetch(f"{API}/acts/{eli}"))


def text_file(eli: str, kind: str, refresh: bool = False) -> bytes:
    """The published text of one entry (kind 'html' or 'pdf'). An entry's text doesn't change once
    published, so it is kept in the cache; metadata is always fetched fresh."""
    path = cache_dir() / f"{eli.replace('/', '_')}.{kind}"
    if path.exists() and path.stat().st_size and not refresh:
        return path.read_bytes()
    data = fetch(f"{API}/acts/{eli}/text.{kind}")
    if data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return data


# ---------------------------------------------------------------- HTML text

VOID = {"br", "img", "hr", "meta", "link", "input", "col", "area", "base", "wbr", "source"}
BLOCK = {"div", "p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "table", "br", "section", "article", "body"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict, parent: Node | None):
        self.tag, self.attrs, self.children, self.parent = tag, attrs, [], parent

    def classes(self) -> set[str]:
        return set((self.attrs.get("class") or "").split())


class TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, {k: (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def html_tree(data: bytes) -> Node:
    b = TreeBuilder()
    b.feed(data.decode("utf-8", errors="replace"))
    b.close()
    return b.root


def walk(node: Node):
    yield node
    for c in node.children:
        if isinstance(c, Node):
            yield from walk(c)


def node_text(node: Node) -> str:
    return "".join(c if isinstance(c, str) else node_text(c) for c in node.children)


def render(node: Node, footnotes: list[tuple[str, str]]) -> list[str]:
    """Text of a node as lines. Superscript numbers become [n] (as in 449[1]); footnote markers are
    taken out of the text and their text collected in `footnotes`."""
    out: list[str] = [""]

    def emit(s: str):
        out[-1] += s

    def visit(n):
        if isinstance(n, str):
            emit(n)
            return
        if n.tag in ("script", "style", "head", "title"):
            return
        cls = n.classes()
        if n.tag == "a" and "gloss-link" in cls:
            mark = ""
            body = ""
            for d in walk(n):
                if d.tag == "sup":
                    mark = node_text(d).strip()
                if "tooltip-text" in d.classes():
                    body = node_text(d)
            footnotes.append((mark, re.sub(r"\s+", " ", body).strip()))
            return
        if n.tag == "sup":
            t = node_text(n).strip()
            emit(f"[{t}]" if re.fullmatch(r"\d+[a-z]?", t) else t)
            return
        block = n.tag in BLOCK
        if block:
            out.append("")
        for c in n.children:
            visit(c)
        if block:
            out.append("")

    visit(node)
    lines = [re.sub(r"\s+", " ", ln.replace("\xa0", " ")).strip() for ln in out]
    return merge_titles([ln for ln in lines if ln])


TITLE_ONLY = re.compile(
    r"^(Art\.\s*\d+[a-z]*(\[\d+\])?\.|§\s*\d+[a-z]*(\[\d+\])?\.|\d+[a-z]*(\[\d+\])?[.)]|[a-z]{1,3}(\[\d+\])?\)|[–-])$")


def merge_titles(lines: list[str]) -> list[str]:
    """'1.' on its own line followed by its text -> '1. text'."""
    merged: list[str] = []
    for ln in lines:
        if merged and TITLE_ONLY.match(merged[-1]) and not TITLE_ONLY.match(ln):
            merged[-1] = f"{merged[-1]} {ln}"
        else:
            merged.append(ln)
    return merged


def html_article(data: bytes, base: str, sup: str | None):
    """(lines, footnotes, unit id) of an article, or None. Articles quoted inside the notice
    (ids under its own pass_/pint_ units) are used only when the act itself has no such article."""
    want = f"arti_{base}" + (f"_{sup}" if sup else "")
    found = []
    for n in walk(html_tree(data)):
        if n.tag == "div" and "unit_arti" in n.classes():
            uid = n.attrs.get("id", "")
            if uid.split("-")[-1].lower() == want:
                found.append(n)
    if not found:
        return None
    own = [n for n in found if not n.attrs.get("id", "").startswith(("pass_", "pint_"))]
    node = (own or found)[0]
    notes: list[tuple[str, str]] = []
    return render(node, notes), notes, node.attrs.get("id", "")


def html_notice(data: bytes) -> list[str]:
    """The notice (obwieszczenie) before the annex that holds the consolidated text."""
    root = html_tree(data)
    part = next((n for n in walk(root) if n.tag == "section" and n.attrs.get("id") == "part_1"), None)
    if part is not None:
        return render(part, [])
    body = next((n for n in walk(root) if n.tag == "body"), root)
    lines = render(body, [])
    cut = next((i for i, ln in enumerate(lines) if ln.startswith("Załącznik do obwieszczenia")), None)
    return lines[:cut] if cut is not None else []


# ---------------------------------------------------------------- PDF text

HEADER = re.compile(r"^\s*(Dziennik Ustaw|Monitor Polski)\s*[–-]\s*\d+\s*[–-]\s*Poz\.\s*\d+\s*$")
FOOTNOTE_START = re.compile(r"^\s*(\d+)\)\s")
ART_HEAD = re.compile(r"^Art\.\s*(\d+[a-z]*)\s*(?:\[(\d+)\])?\.(?:(\d+)\))?(?=\s|$|\()")
STRUCTURE = re.compile(r"^(KSIĘGA|CZĘŚĆ|TYTUŁ|DZIAŁ)\b|^(Rozdział|Oddział)\s+[0-9IVXLCDM]+[a-z]?\b")
UNIT_START = re.compile(r"^(Art\.\s|§\s*\d|\d+[a-z]*(\[\d+\])?\.\s|\d+[a-z]*(\[\d+\])?\)\s|[a-z]{1,3}(\[\d+\])?\)\s|[–-]\s)")
# A footnote marker sits right after a full stop, a bracket or a letter, with no space: "§ 1.7)",
# "(utracił moc)2)", "cywilny1)". Only numbers that are footnotes of this document count.
MARKER = re.compile(r"(?<=[.)\]A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ])(\d{1,3})\)")


def pdf_pages(data: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise Problem("This text is published only as a PDF, and reading it needs pypdf. "
                      "Run the script with `uv run`, which installs it.") from e
    import io
    return [(p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]


def split_page(text: str) -> tuple[list[str], dict[str, str]]:
    """Body lines and footnotes of one page. The running header goes; footnotes are the block
    after the last empty line when it starts with 'n) '."""
    lines = text.splitlines()
    if lines and HEADER.match(lines[0]):
        lines = lines[1:]
    lines = [ln for ln in lines if not HEADER.match(ln)]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    notes: dict[str, str] = {}
    blanks = [i for i, ln in enumerate(lines) if not ln.strip()]
    if blanks and blanks[-1] > 0:
        b = blanks[-1]
        tail = lines[b + 1:]
        if tail and FOOTNOTE_START.match(tail[0]):
            lines = lines[:b]
            num = None
            for ln in tail:
                m = FOOTNOTE_START.match(ln)
                if m:
                    num = m[1]
                    notes[num] = ln[m.end():].strip()
                elif num:
                    notes[num] = join_text(notes[num], ln.strip())
    return [ln.rstrip() for ln in lines if ln.strip()], notes


def join_text(a: str, b: str) -> str:
    """Join two wrapped lines; a line-end hyphen between lowercase letters is the typesetter's."""
    a = a.rstrip()
    if re.search(r"[a-ząćęłńóśźż]-$", a) and re.match(r"[a-ząćęłńóśźż]", b):
        return a[:-1] + b
    return f"{a} {b}"


def paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    for ln in lines:
        ln = re.sub(r"[ \t]{2,}", " ", ln.strip())
        if out and not UNIT_START.match(ln):
            out[-1] = join_text(out[-1], ln)
        else:
            out.append(ln)
    return out


def pdf_document(data: bytes):
    """[(page, line)], {footnote number: text}, index of the first line of the annex (0 if none)."""
    body: list[tuple[int, str]] = []
    notes: dict[str, str] = {}
    for i, page in enumerate(pdf_pages(data), start=1):
        lines, fn = split_page(page)
        notes.update(fn)
        body += [(i, ln) for ln in lines]
    annex = next((k + 1 for k, (_, ln) in enumerate(body) if ln.startswith("Załącznik do obwieszczenia")), 0)
    return body, notes, annex


def pdf_article(data: bytes, base: str, sup: str | None):
    body, notes, annex = pdf_document(data)
    heads = [(k, ART_HEAD.match(ln)) for k, (_, ln) in enumerate(body) if k >= annex and ART_HEAD.match(ln)]
    for idx, (k, m) in enumerate(heads):
        if m[1].lower() == base and (m[2] or None) == sup:
            end = heads[idx + 1][0] if idx + 1 < len(heads) else len(body)
            chunk = body[k:end]
            for j in range(1, len(chunk)):
                if STRUCTURE.match(chunk[j][1]):
                    chunk = chunk[:j]
                    break
            marks: list[tuple[str, str]] = []
            lines = []
            for _, ln in chunk:
                unit = re.match(r"^(?:Art\.\s*\d+[a-z]*\s*(?:\[\d+\])?\.\s*)?(§\s*\d+[a-z]*(?:\[\d+\])?|\d+[a-z]*(?:\[\d+\])?(?=[.)]))", ln)
                found = [mm[1] for mm in MARKER.finditer(ln) if mm[1] in notes]
                for n in found:
                    marks.append((unit[1] if unit else "", n))
                lines.append(MARKER.sub(lambda mm: "" if mm[1] in notes else mm[0], ln))
            lines[0] = re.sub(r"^(Art\.\s*\d+[a-z]*)\s+\[", r"\1[", lines[0])
            pages = sorted({p for p, _ in chunk})
            return paragraphs(lines), [(f"{u + ': ' if u else ''}{n})", notes.get(n, "(footnote text not found)"))
                                       for u, n in marks], pages
    return None


def pdf_notice(data: bytes) -> list[str]:
    body, _, annex = pdf_document(data)
    return paragraphs([ln for _, ln in body[: max(annex - 1, 0)]]) if annex else []


def heads_pdf(data: bytes) -> list[tuple[str, str | None]]:
    body, _, annex = pdf_document(data)
    return [(m[1].lower(), m[2]) for k, (_, ln) in enumerate(body) if k >= annex and (m := ART_HEAD.match(ln))]


def heads_html(data: bytes) -> list[tuple[str, str | None]]:
    out = []
    for n in walk(html_tree(data)):
        if n.tag == "div" and "unit_arti" in n.classes():
            uid = n.attrs.get("id", "")
            if uid.startswith(("pass_", "pint_")):
                continue
            m = re.fullmatch(r"arti_(\d+[a-z]*)(?:_(\d+))?", uid.split("-")[-1], re.I)
            if m:
                out.append((m[1].lower(), m[2]))
    return out


def nearby(heads: list[tuple[str, str | None]], base: str, sup: str | None) -> tuple[list[str], list[str]]:
    """Articles with the same number, and superscript articles the number may have lost
    ('4491' typed for 449^1, as happens when a Word document loses its formatting)."""
    num = re.match(r"\d+", base)[0]
    same = [article_label(b, s) for b, s in heads if re.match(r"\d+", b)[0] == num]
    lost = []
    if not sup and base.isdigit():
        for k in (1, 2):
            if len(base) > k and (base[:-k], base[-k:]) in heads:
                lost.append(article_label(base[:-k], base[-k:]))
    return same, lost


# ---------------------------------------------------------------- what an entry is

def is_consolidated(m: dict) -> bool:
    t = (m.get("title") or "").lower()
    return t.startswith("obwieszczenie") and "jednolitego tekstu" in t


def latest_tj(m: dict) -> str | None:
    refs = (m.get("references") or {}).get(TJ_REF) or []
    return max((r["id"] for r in refs), key=eli_key) if refs else None


def resolve_text(eli: str) -> tuple[str, dict, str]:
    """Which entry to read for `article`: a consolidated text as given; for an act that has
    consolidated texts, the latest one; otherwise the act itself."""
    m = meta(eli)
    if is_consolidated(m):
        return eli, m, "a consolidated text, as given"
    tj = latest_tj(m)
    if tj:
        return tj, meta(tj), f"the latest consolidated text of {eli}"
    return eli, m, "the act as published (it has no consolidated text)"


def load_text(eli: str, m: dict, refresh: bool) -> tuple[str, bytes]:
    if m.get("textHTML"):
        data = text_file(eli, "html", refresh)
        if data.strip():
            return "html", data
    if m.get("textPDF"):
        data = text_file(eli, "pdf", refresh)
        if data:
            return "pdf", data
    raise Problem(f"{eli} has no text the API can give (textHTML={m.get('textHTML')}, "
                  f"textPDF={m.get('textPDF')}). Nothing was verified.")


# ---------------------------------------------------------------- commands

def cmd_find(args) -> dict:
    q = urllib.parse.urlencode({"publisher": args.publisher, "title": args.words, "limit": 500})
    data = json.loads(fetch(f"{API}/acts/search?{q}"))
    amending = re.compile(r"\bo zmianie\b|\bzmieniając|^obwieszczenie|^ustawa o zmianie", re.I)
    items = [a for a in data.get("items", []) if not amending.search(a.get("title", ""))]
    rank = {"akt posiada tekst jednolity": 0, "obowiązujący": 1}
    items.sort(key=lambda a: (rank.get(a.get("status"), 2), eli_key(a["ELI"])))
    return {"query": args.words, "results": [
        {"eli": a["ELI"], "title": a.get("title"), "status": a.get("status"), "in_force": a.get("inForce")}
        for a in items[:25]], "left_out_as_amending": data.get("totalCount", 0) - len(items)}


def print_find(r: dict):
    if not r["results"]:
        print(f"No base act found for {r['query']!r}. Try fewer or different words from the title.")
        return
    for a in r["results"]:
        print(f"{a['eli']:<14} {a['status'] or '':<28} {a['title']}")
    print(f"\n({r['left_out_as_amending']} amending acts and notices left out.)")


def notice_cutoffs(notice: str) -> tuple[dt.date | None, dt.date | None]:
    """Notices give their cut-off in one of two ways: the law "as it stood on" a day
    (stanu prawnego na dzień), or the changes "from provisions published before" a day
    (ogłoszonych przed dniem)."""
    state = re.search(r"stanu prawnego na dzień\s+(\d{1,2}\s+\S+\s+\d{4})", notice)
    before = re.search(r"ogłoszonych przed dniem\s+(\d{1,2}\s+\S+\s+\d{4})", notice)
    return (polish_date(state[1]) if state else None), (polish_date(before[1]) if before else None)


def cmd_act(args) -> dict:
    eli = parse_eli(args.act)
    m = meta(eli)
    if is_consolidated(m):
        base = ((m.get("references") or {}).get("Tekst jednolity dla aktu") or [{}])[0].get("id")
        if not base:
            raise Problem(f"{eli} is a consolidated text, but the API names no act it belongs to.")
        given_tj = eli
        eli, m = base, meta(base)
    else:
        given_tj = None
    refs = m.get("references") or {}
    tjs = sorted((r["id"] for r in refs.get(TJ_REF) or []), key=eli_key)
    out = {
        "act": eli, "title": m.get("title"), "status": m.get("status"), "in_force": m.get("inForce"),
        "you_gave_a_consolidated_text": given_tj, "consolidated_texts": tjs, "latest": None,
        "notice": [], "legal_state_on": None, "changes_after": [], "tribunal_after": [],
    }
    if not tjs:
        return out
    tj = tjs[-1]
    tm = meta(tj)
    kind, data = load_text(tj, tm, args.refresh)
    notice = html_notice(data) if kind == "html" else pdf_notice(data)
    joined = " ".join(notice)
    state_date, published_before = notice_cutoffs(joined)
    announced = dt.date.fromisoformat(tm["announcementDate"]) if tm.get("announcementDate") else None
    since = state_date or published_before or announced
    cutoff = published_before or announced
    out.update(latest={"eli": tj, "cite": dzu(tj), "announced": tm.get("announcementDate"),
                       "published": tm.get("promulgation"), "read_from": kind},
               notice=notice, legal_state_on=state_date.isoformat() if state_date else None,
               changes_published_before=published_before.isoformat() if published_before else None)
    today = dt.date.today()
    tj_year = eli_key(tj)[1]
    for r in sorted(refs.get("Akty zmieniające") or [], key=lambda r: eli_key(r["id"])):
        change = dt.date.fromisoformat(r["date"]) if r.get("date") else None
        if eli_key(r["id"])[1] < tj_year - 1 and (change is None or since is None or change <= since):
            continue
        am = meta(r["id"])
        published = dt.date.fromisoformat(am["promulgation"]) if am.get("promulgation") else None
        after_state = since is not None and change is not None and change > since
        after_tj = cutoff is not None and published is not None and (
            published >= cutoff if published_before else published > cutoff)
        if not (after_state or after_tj):
            continue
        pos = eli_key(r["id"])[2]
        out["changes_after"].append({
            "eli": r["id"], "cite": dzu(r["id"]), "title": am.get("title"), "published": am.get("promulgation"),
            "changes_this_act_from": r.get("date"), "in_force_now": bool(change and change <= today),
            "published_after_the_consolidated_text": after_tj,
            "position_named_in_the_notice": bool(re.search(rf"poz\.\s*{pos}\b", joined)),
        })
    for r in refs.get("Orzeczenie TK") or []:
        if eli_key(r["id"]) > eli_key(tj):
            rm = meta(r["id"])
            out["tribunal_after"].append({"eli": r["id"], "cite": dzu(r["id"]), "title": rm.get("title"),
                                          "published": rm.get("promulgation")})
    return out


def print_act(r: dict):
    if r["you_gave_a_consolidated_text"]:
        print(f"{r['you_gave_a_consolidated_text']} is a consolidated text of {r['act']}.\n")
    print(f"Act:     {r['act']}  {r['title']}")
    print(f"Status:  {r['status']} ({r['in_force']})")
    if r["in_force"] != "IN_FORCE":
        print("         NOT IN FORCE according to the API. Check whether the draft should cite it at all.")
    if not r["latest"]:
        print("\nNo consolidated text. Cite the act as published, with its amendments ('z późn. zm.').")
        return
    lt = r["latest"]
    print(f"\nLatest consolidated text: {lt['cite']} ({lt['eli']}), notice of {lt['announced']}, "
          f"published {lt['published']}")
    if r["legal_state_on"]:
        print(f"It gives the law as it stood on {r['legal_state_on']} (from the notice).")
    if r.get("changes_published_before"):
        print(f"It takes in changes from provisions published before {r['changes_published_before']} (from the notice).")
    if not r["legal_state_on"] and not r.get("changes_published_before"):
        print("The notice gives no cut-off date the checker could read; read it below.")
    older = [dzu(e) for e in r["consolidated_texts"][:-1]][-4:]
    if older:
        print(f"Older consolidated texts (a draft that cites one of these is out of date): {', '.join(reversed(older))}")
    print("\nThe notice, word for word (what the consolidated text includes and what it leaves out):")
    for ln in r["notice"]:
        print(f"  {ln}")
    print("\nChanges to this act recorded after that cut-off (check each against the cited articles):")
    if not r["changes_after"]:
        print("  none")
    for c in r["changes_after"]:
        when = c["changes_this_act_from"] or "date not given"
        state = "in force" if c["in_force_now"] else "NOT YET IN FORCE"
        flags = []
        if c["published_after_the_consolidated_text"]:
            flags.append("published after the consolidated text's cut-off, so it is NOT in it")
        if c["position_named_in_the_notice"]:
            flags.append("its position number appears in the notice")
        print(f"  {c['cite']} ({c['eli']}): changes this act from {when} ({state}); published {c['published']}")
        print(f"    {c['title']}")
        if flags:
            print(f"    {'; '.join(flags)}")
    print("\nConstitutional Tribunal rulings on this act published after the consolidated text:")
    if not r["tribunal_after"]:
        print("  none recorded (a ruling never published in Dziennik Ustaw does not appear here)")
    for t in r["tribunal_after"]:
        print(f"  {t['cite']}: {t['title']} (published {t['published']})")


def cmd_article(args) -> dict:
    base, sup = parse_article(args.number)
    given = parse_eli(args.act)
    eli, m, why = resolve_text(given)
    kind, data = load_text(eli, m, args.refresh)
    label = article_label(base, sup)
    out = {"given": given, "read": eli, "why": why, "title": m.get("title"), "article": label,
           "source": f"{API}/acts/{eli}/text.{kind}", "format": kind, "found": False}
    got = html_article(data, base, sup) if kind == "html" else pdf_article(data, base, sup)
    if not got:
        out["nearby"], out["maybe_meant"] = nearby(heads_html(data) if kind == "html" else heads_pdf(data), base, sup)
        return out
    if kind == "html":
        lines, notes, uid = got
        out.update(found=True, text=lines, footnotes=notes, unit=uid)
    else:
        lines, notes, pages = got
        out.update(found=True, text=lines, footnotes=notes, pages=pages)
    out["repealed"] = bool(re.fullmatch(r"Art\.\s*\S+\s*\((uchylony|uchylona|pominięty|skreślony|utracił moc)\)\.?",
                                        " ".join(lines)))
    return out


def print_article(r: dict):
    print(f"Read: {dzu(r['read'])} ({r['read']}), {r['why']}")
    print(f"      {r['title']}")
    where = f", page {', '.join(map(str, r['pages']))}" if r.get("pages") else ""
    print(f"From: {r['source']}{where}")
    if not r["found"]:
        print(f"\n{r['article']} is not in this text.")
        if r.get("nearby"):
            print(f"Articles with the same number there: {', '.join(r['nearby'])}")
        if r.get("maybe_meant"):
            print(f"Maybe a superscript was lost: {', '.join(r['maybe_meant'])} exists. "
                  "Write it as 449^1 or 449¹.")
        return
    if r["format"] == "pdf":
        print("Note: text taken from the PDF by machine. Line-end hyphens are joined and spacing can be off;"
              " check the wording on the page above before quoting it.")
    if r.get("repealed"):
        print("\nThis article is NO LONGER LAW in this text: repealed (uchylony), struck down (utracił moc) or "
              "left out (pominięty). The footnotes below say why and since when.")
    print()
    for ln in r["text"]:
        print(ln)
    if r["footnotes"]:
        print("\nFootnotes on this article (where its wording comes from):")
        for mark, txt in r["footnotes"]:
            print(f"  {mark} {txt}")


def cmd_text(args) -> dict:
    eli = parse_eli(args.act)
    m = meta(eli)
    kind, data = load_text(eli, m, args.refresh)
    if kind == "html":
        lines = [(None, ln) for ln in render(html_tree(data), [])]
    else:
        body, _, _ = pdf_document(data)
        lines = body
    out = {"eli": eli, "title": m.get("title"), "format": kind, "source": f"{API}/acts/{eli}/text.{kind}"}
    if args.grep:
        rx = re.compile(args.grep, re.I)
        hits = []
        for i, (page, ln) in enumerate(lines):
            if rx.search(ln):
                ctx = [x for _, x in lines[max(0, i - args.context): i + args.context + 1]]
                hits.append({"page": page, "line": ln, "context": ctx})
        out["matches"] = hits
    else:
        out["text"] = [ln for _, ln in lines]
    return out


def print_text(r: dict):
    print(f"{dzu(r['eli'])} ({r['eli']}): {r['title']}\nFrom: {r['source']}\n")
    if "matches" in r:
        if not r["matches"]:
            print("No line matches.")
        for h in r["matches"]:
            print(f"--- {'page ' + str(h['page']) if h['page'] else 'match'}")
            for ln in h["context"]:
                print(f"  {ln}")
        return
    for ln in r["text"]:
        print(ln)


ART_CITE = re.compile(
    r"\bart(?:ykuł\w*|\.)\s*\d+[a-z]*(?:\s*(?:\^|\[)\s*\d+\s*\]?|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)?"
    r"(?:\s*(?:ust\.|§|pkt|lit\.|zd\.|zdanie|tiret)\s*\d*[a-z]*(?:\s*(?:\^|\[)\s*\d+\s*\]?|[¹²³⁴⁵⁶⁷⁸⁹⁰]+)?)*", re.I)
DZU_CITE = re.compile(
    r"\b(?:Dz\.\s*U(?:rz)?|M\.\s*P)\.\s*(?:z\s*)?(?:\d{4}\s*r\.\s*)?(?:Nr\s*\d+\s*,?\s*)?poz\.\s*\d+"
    r"(?:\s*(?:,|i)\s*\d+)*(?:\s*,?\s*z\s*późn\.\s*zm\.)?", re.I)
PARA_CITE = re.compile(r"§\s*\d+[a-z]*(?:\s*(?:ust\.|pkt)\s*\d+[a-z]*)*")
ACT_NAME = re.compile(
    r"\b(?:Kodeks\w*\s+[a-ząćęłńóśźż]+(?:\s+[a-ząćęłńóśźż]+)?|ustaw[aąyie]\s+z\s+dnia\s+\d{1,2}\s+\S+\s+\d{4}\s*r\.[^,;()\n]{0,120}"
    r"|rozporządzeni[aeu]\s+[^,;()\n]{0,160}?z\s+dnia\s+\d{1,2}\s+\S+\s+\d{4}\s*r\.)"
    r"|\b(?:k\.c|k\.p\.c|k\.p|k\.k|k\.p\.k|k\.s\.h|k\.r\.o|k\.p\.a|k\.w|k\.k\.w|o\.p|p\.p\.s\.a|u\.p\.t\.u)\.", re.I)


def document_lines(path: Path) -> list[str]:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        out = []
        for para in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            text = ""
            for run in re.findall(r"<w:r[ >].*?</w:r>", para, re.S):
                t = "".join(re.findall(r"<w:t[^>]*>(.*?)</w:t>", run, re.S))
                # A superscript run ("449" + superscript "1") is kept as 449^1, not read as 4491.
                if re.search(r'<w:vertAlign w:val="superscript"', run) and re.fullmatch(r"\d+", t):
                    t = "^" + t
                text += t
            out.append(html.unescape(text))
        return out
    if suffix == ".pdf":
        return "\n".join(pdf_pages(path.read_bytes())).splitlines()
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def cmd_cites(args) -> dict:
    path = Path(args.file)
    if not path.exists():
        raise Problem(f"No such file: {path}")
    found = []
    for no, line in enumerate(document_lines(path), start=1):
        taken: list[tuple[int, int]] = []
        for kind, rx in (("article", ART_CITE), ("journal", DZU_CITE), ("act", ACT_NAME), ("paragraph", PARA_CITE)):
            for m in rx.finditer(line):
                if any(a <= m.start() < b for a, b in taken):
                    continue
                taken.append((m.start(), m.end()))
                found.append({"line": no, "kind": kind, "text": re.sub(r"\s+", " ", m[0]).strip(),
                              "context": line.strip()[:200]})
    found.sort(key=lambda f: (f["line"], f["context"].find(f["text"])))
    return {"file": str(path), "citations": found}


def print_cites(r: dict):
    if not r["citations"]:
        print("No citations found. Check the draft by eye: unusual forms are missed.")
        return
    width = max(len(str(c["line"])) for c in r["citations"])
    for c in r["citations"]:
        print(f"{c['line']:>{width}}  {c['kind']:<9} {c['text']}")
    counts = {}
    for c in r["citations"]:
        counts[c["kind"]] = counts.get(c["kind"], 0) + 1
    print(f"\n{len(r['citations'])} found: " + ", ".join(f"{v} {k}" for k, v in counts.items()) +
          ". This list is a start, not a guarantee: read the draft for citations it missed.")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="eli.py", description=__doc__.split("\n\n")[0])
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p.add_argument("--refresh", action="store_true", help="download texts again instead of using the cache")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find", help="base acts whose title matches")
    f.add_argument("words")
    f.add_argument("--publisher", default="DU", choices=["DU", "MP"])
    a = sub.add_parser("act", help="latest consolidated text and changes after it")
    a.add_argument("act")
    r = sub.add_parser("article", help="one article, word for word")
    r.add_argument("act")
    r.add_argument("number")
    t = sub.add_parser("text", help="the official text of exactly this entry")
    t.add_argument("act")
    t.add_argument("--grep", help="print only lines matching this regular expression (case-insensitive)")
    t.add_argument("--context", type=int, default=2)
    c = sub.add_parser("cites", help="list the citations in a draft")
    c.add_argument("file")
    args = p.parse_args(argv)
    run, show = {"find": (cmd_find, print_find), "act": (cmd_act, print_act), "article": (cmd_article, print_article),
                 "text": (cmd_text, print_text), "cites": (cmd_cites, print_cites)}[args.cmd]
    try:
        result = run(args)
    except Problem as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        show(result)
    if args.cmd == "article" and not result["found"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
