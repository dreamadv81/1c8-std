#!/usr/bin/env python3
# 1c8-std/std8.py
"""Стандарты 1С 8.x: show / find / list по выжимкам.

Индекса нет: порядок статей — из INDEX.md (оглавление стандартов), выжимки читаются на лету.
Поиск идёт по выжимкам. Полного текста статей в навыке нет: `show N --full` печатает ссылку на
статью на its.1c.ru, а если рядом собрана книга `book/` — её файл.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 788 «Новые и измененные разделы» — журнал изменений: правил не несёт, а термины несёт почти всех
# разделов книги, и в find стоял бы первой строкой на любой запрос (замер итоговой рецензии: восемь
# из двенадцати типовых слов). `show 788` и `list` его отдают — там он и нужен.
CHANGES_ID = 788
ITS_URL = "https://its.1c.ru/db/v8std/content/%d/hdoc"
_INDEX_DOC = re.compile(r"^( *)- \[(\d+) (.*?)\]\(<(?:book|digest)/(.*?)>\)")
_INDEX_DIR = re.compile(r"^( *)- \*\*(.*?)\*\*$")
_FRONT_LINE = re.compile(r"^([a-z0-9_]+): (.*)$")
_SCOPE_SHORT = (("управляемое приложение", "упр"), ("мобильное приложение", "моб"), ("обычное приложение", "обыч"))


def norm(s):
    return (s or "").lower().replace("ё", "е")


class Article:
    __slots__ = ("id", "title", "rel", "section")

    def __init__(self, id_, title, rel, section):
        self.id, self.title, self.rel, self.section = id_, title, rel, section


def read_index(root):
    """Статьи в порядке оглавления: (id, название, путь от book/, [разделы сверху вниз])."""
    path = root / "INDEX.md"
    if not path.is_file():
        raise FileNotFoundError(f"нет {path}: в навыке нет оглавления")
    out, stack = [], []
    for line in path.read_text("utf-8").splitlines():
        m = _INDEX_DIR.match(line)
        if m:
            depth = len(m.group(1)) // 2
            del stack[depth:]
            stack.append(m.group(2))
            continue
        m = _INDEX_DOC.match(line)
        if m:
            depth = len(m.group(1)) // 2
            del stack[depth:]
            out.append(Article(int(m.group(2)), m.group(3), m.group(4), list(stack)))
    if not out:
        # Оглавление, собранное внутрь книги (--index), ведёт на статьи без префикса `book/`.
        # Пустой список отдал бы «ничего не нашлось» — догадка вместо отказа.
        raise ValueError(f"{path}: статей не разобрано — в оглавлении нет ссылок на выжимки")
    return out


def front_matter(text):
    """YAML-заголовок выжимки или статьи: значения — строки JSON, числа, массивы (как их пишет сборка)."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        m = _FRONT_LINE.match(line)
        if m:
            try:
                meta[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                meta[m.group(1)] = m.group(2)
    return meta, text[end + 5:]


def digest_path(root, art):
    return root / "digest" / art.rel


def scope_short(scope):
    if not scope:
        return "?"
    return ", ".join(short for full, short in _SCOPE_SHORT if full in scope) or scope


def _words(text):
    return re.findall(r"[^\W_]+", norm(text))


def cmd_show(root, arts, id_, full):
    art = next((a for a in arts if a.id == id_), None)
    if art is None:
        print(f"статьи {id_} нет в оглавлении", file=sys.stderr)
        return 1
    path = root / "book" / art.rel if full else digest_path(root, art)
    if not path.is_file():
        if full:
            # книги в навыке нет: полный текст — на сайте
            print(f"{id_} {art.title}: полный текст — {ITS_URL % id_}")
            return 0
        print(f"выжимки {id_} нет; статья: {ITS_URL % id_}", file=sys.stderr)
        return 1
    print(f"— {path.relative_to(root).as_posix()}")
    print(path.read_text("utf-8"), end="")
    return 0


def _in_section(art, section):
    key = norm(section)
    return any(norm(s).startswith(key) for s in art.section) or key in norm(" / ".join(art.section))


def cmd_find(root, arts, words, section, limit):
    # Ключи режутся на слова тем же правилом, что корпус: иначе «клиент-серверное» не найдёт ничего,
    # а пустой ответ навыка по стандартам читается как «стандарт об этом молчит».
    keys = _words(" ".join(words))
    if not keys:
        print("find: нужно хотя бы одно слово", file=sys.stderr)
        return 2
    if limit < 1:
        print(f"find: -n {limit} — потолок строк меньше одной", file=sys.stderr)
        return 2
    found = 0
    for art in arts:
        if art.id == CHANGES_ID:      # журнал изменений, см. CHANGES_ID
            continue
        if section and not _in_section(art, section):
            continue
        path = digest_path(root, art)
        if not path.is_file():
            continue
        # Тело и название (§6), без заголовка YAML: scope, book и source_sha256 нашлись бы в каждой выжимке.
        tokens = _words(art.title + "\n" + front_matter(path.read_text("utf-8"))[1])
        if all(any(t.startswith(k) for t in tokens) for k in keys):
            found += 1
            if found <= limit:
                where = f"  · {' / '.join(art.section)}" if art.section else ""
                print(f"{art.id}  {art.title}{where}")
    if found > limit:
        print(f"… ещё {found - limit} (уточните слова, --in или -n)")
    return 0 if found else 1


def cmd_list(root, arts, section):
    if not section:
        counts = {}
        for art in arts:
            for depth in range(1, len(art.section) + 1):
                key = tuple(art.section[:depth])
                counts[key] = counts.get(key, 0) + 1
        seen = set()
        for art in arts:
            if not art.section:
                print(f"{art.id}  {art.title}  [{scope_short(_scope(root, art))}]")
            for depth in range(1, len(art.section) + 1):
                key = tuple(art.section[:depth])
                if key not in seen:
                    seen.add(key)
                    print(f"{'  ' * (depth - 1)}{key[-1]}: {counts[key]}")
        return 0
    rows = [a for a in arts if _in_section(a, section)]
    for art in rows:
        missing = "" if digest_path(root, art).is_file() else "  (нет выжимки)"
        print(f"{art.id}  {art.title}  [{scope_short(_scope(root, art))}]{missing}")
    if not rows:
        print(f"раздела «{section}» нет; разделы — std8.py list", file=sys.stderr)
        return 1
    return 0


def _scope(root, art):
    for path in (root / "book" / art.rel, digest_path(root, art)):
        if path.is_file():
            return front_matter(path.read_text("utf-8"))[0].get("scope")
    return None


def main(argv=None, root=ROOT):
    ap = argparse.ArgumentParser(prog="std8.py", description="Стандарты 1С 8.x: выжимки статей v8std.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("show", help="выжимка статьи по номеру; --full — ссылка на статью")
    p.add_argument("id", type=int)
    p.add_argument("--full", action="store_true")
    p = sub.add_parser("find", help="выжимки, где встречаются все слова (по началу слова)")
    p.add_argument("words", nargs="*")
    p.add_argument("--in", dest="section", default=None, help="раздел оглавления: подстрока названия, любой уровень")
    p.add_argument("-n", type=int, default=20, help="потолок строк (умолчание 20)")
    p = sub.add_parser("list", help="разделы, а с аргументом — статьи раздела")
    p.add_argument("section", nargs="?", default=None)
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 0 if e.code == 0 else 2      # --help и -h печатают справку и выходят успехом
    try:
        arts = read_index(root)
    except (FileNotFoundError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2
    if args.cmd == "show":
        return cmd_show(root, arts, args.id, args.full)
    if args.cmd == "find":
        return cmd_find(root, arts, args.words, args.section, args.n)
    return cmd_list(root, arts, args.section)


if __name__ == "__main__":
    sys.exit(main())
