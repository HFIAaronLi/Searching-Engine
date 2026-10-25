import os
import re
import time
import queue
import threading
import webbrowser
from dataclasses import dataclass
from typing import List
from urllib.parse import quote_plus

import requests
import yaml
from bs4 import BeautifulSoup
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
CONFIG_PATH = os.path.expanduser("~/.tsearch.yaml")


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    score: float = 0.0


LANG = {
    "zh": {
        "prompt": "搜索",
        "results": "结果",
        "next": "下一页",
        "prev": "上一页",
        "open": "打开",
        "exit": "退出",
        "new": "新话题",
        "pool": "池子",
        "lang": "语言",
        "help": "帮助",
        "no_results": "没有找到结果",
        "current_query": "当前查询",
        "current_page": "当前页",
        "choose_pool": "当前搜索池",
        "supported_pools": "可用池子",
        "engines_failed": "以下引擎请求失败或未返回结果",
        "lang_hint": "语言: {lang}（auto = 按查询自动识别中/英）",
        "pool_hint": "搜索池: {pool}",
        "lang_current": "当前语言: {lang}（可选: auto | zh | en）",
        "lang_switched": "语言已切换: {lang}（已保存）",
        "pool_saved": "搜索池已设置: {pool}（已保存）",
        "new_ready": "新话题已就绪。",
        "cmd_help": """
可用命令：
  :new              开始新话题
  :next / n         下一页
  :prev / p         上一页
  :open <编号>      打开某条结果
  :pool <源1,源2>   设置搜索池（不带参数查看可用引擎）
  :lang auto|zh|en  切换语言（auto 按查询自动识别，中英文都能搜）
  :exit / q         退出

说明：
  - 每条结果返回：标题 + 摘要开头（第一段/第二段前几个字）+ 网址 + 来源
  - 语言和搜索池会自动保存到 ~/.tsearch.yaml，下次启动直接生效
""",
    },
    "en": {
        "prompt": "Search",
        "results": "Results",
        "next": "Next",
        "prev": "Prev",
        "open": "Open",
        "exit": "Exit",
        "new": "New topic",
        "pool": "Pool",
        "lang": "Language",
        "help": "Help",
        "no_results": "No results found",
        "current_query": "Current query",
        "current_page": "Current page",
        "choose_pool": "Current search pool",
        "supported_pools": "Supported pools",
        "engines_failed": "Engines that failed or returned nothing",
        "lang_hint": "Language: {lang} (auto = auto-detect zh/en from query)",
        "pool_hint": "Search pool: {pool}",
        "lang_current": "Current language: {lang} (choices: auto | zh | en)",
        "lang_switched": "Language switched to: {lang} (saved)",
        "pool_saved": "Search pool set: {pool} (saved)",
        "new_ready": "New topic ready.",
        "cmd_help": """
Commands:
  :new              Start a new topic
  :next / n         Next page
  :prev / p         Previous page
  :open <id>        Open a result
  :pool <s1,s2>     Set search pool (no args to list engines)
  :lang auto|zh|en  Switch language (auto-detects; works for zh & en)
  :exit / q         Quit

Notes:
  - Each result shows: title + snippet head (first/second paragraph) + url + source
  - Language and pool are saved to ~/.tsearch.yaml and reused next launch
""",
    },
}


def t(lang, key):
    lang = "zh" if lang == "auto" else lang
    return LANG.get(lang, LANG["en"]).get(key, key)


def tokenize(text):
    text = (text or "").lower()
    if re.search(r"[\u4e00-\u9fff]", text):
        return [ch for ch in text if ch.strip()]
    return re.findall(r"[a-z0-9]+", text)


def relevance_score(query, title, snippet):
    q = set(tokenize(query))
    tt = set(tokenize(title))
    ss = set(tokenize(snippet))
    if not q:
        return 0.0
    return (len(q & tt) * 3.0 + len(q & ss)) / max(1, len(q))


def is_ad_or_junk(title, snippet, url):
    text = f"{title} {snippet}".lower()
    url = (url or "").lower()
    bad_words = ["广告", "推广", "sponsored", "promoted", "doubleclick", "affiliate"]
    return any(x in text for x in bad_words) or any(x in url for x in bad_words)


def normalize_url(url):
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("www."):
        return "https://" + url
    return url


def dedupe(results):
    seen = set()
    out = []
    for r in results:
        key = r.url.split("#")[0].rstrip("/")
        if key and key not in seen:
            seen.add(key)
            out.append(r)
    return out


def detect_lang(query):
    """按查询内容自动判断语言：中日韩文字 → zh，否则 en"""
    if re.search(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", query or ""):
        return "zh"
    return "en"


def snippet_preview(snippet, head=46, tail=34):
    """取摘要第一段开头 + 第二段开头，超出部分省略"""
    s = re.sub(r"\s+", " ", (snippet or "")).strip()
    if not s:
        return ""
    parts = [p.strip() for p in re.split(r"[。！？!?；;\n]+|(?<=[.!?])\s+(?=[A-Z0-9])", s) if p.strip()]
    first = parts[0]
    second = parts[1] if len(parts) > 1 and len(parts[1]) >= 2 else ""
    if second:
        return f"{first[:head]} … {second[:tail]}"
    if len(s) > head + tail:
        return s[:head] + " … " + s[head:head + tail]
    return s


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg
    except Exception:
        return {}


def save_config(lang, pool):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.safe_dump({"lang": lang, "pool": list(pool)}, f, allow_unicode=True)
    except Exception:
        pass


class BingProvider:
    name = "bing"
    def search(self, query, lang="auto"):
        market = "zh-CN" if lang == "zh" else "en-US"
        url = f"https://www.bing.com/search?q={quote_plus(query)}&count=10&mkt={market}"
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).text
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for li in soup.select("li.b_algo")[:10]:
            a = li.find("a")
            if not a:
                continue
            p = li.find("p")
            out.append(SearchResult(
                title=a.get_text(" ", strip=True),
                url=normalize_url(a.get("href", "")),
                snippet=p.get_text(" ", strip=True) if p else "",
                source=self.name
            ))
        return out


class DuckDuckGoProvider:
    name = "duckduckgo"
    def search(self, query, lang="auto"):
        html = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=3
        ).text
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for a in soup.select(".result__a")[:10]:
            parent = a.find_parent("div", class_="result")
            sn = parent.select_one(".result__snippet") if parent else None
            out.append(SearchResult(
                title=a.get_text(" ", strip=True),
                url=normalize_url(a.get("href", "")),
                snippet=sn.get_text(" ", strip=True) if sn else "",
                source=self.name
            ))
        return out


class BaiduProvider:
    name = "baidu"
    def search(self, query, lang="auto"):
        url = f"https://www.baidu.com/s?wd={quote_plus(query)}&rn=10"
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3).text
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for div in soup.select("div.result, div.c-container")[:10]:
            a = div.find("a")
            if not a:
                continue
            out.append(SearchResult(
                title=a.get_text(" ", strip=True),
                url=normalize_url(a.get("href", "")),
                snippet=div.get_text(" ", strip=True)[:200],
                source=self.name
            ))
        return out


class SearXNGProvider:
    name = "searxng"
    def __init__(self, base_url="http://127.0.0.1:8888"):
        self.base_url = base_url.rstrip("/")
    def search(self, query, lang="auto"):
        r = requests.get(
            f"{self.base_url}/search",
            params={
                "q": query,
                "format": "json",
                "language": "zh-CN" if lang == "zh" else "en-US"
            },
            timeout=3
        )
        data = r.json()
        out = []
        for item in data.get("results", [])[:10]:
            out.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source=self.name
            ))
        return out


class Engine:
    def __init__(self):
        cfg = load_config()
        self.lang = cfg.get("lang", "auto")
        if self.lang not in ("auto", "zh", "en"):
            self.lang = "auto"
        self.lang_code = "en"
        self.page_size = 10
        self.timeout = 4.5
        self.current_query = None
        self.current_page = 1
        self.all_results = []
        self.last_failed = []
        self.providers = {
            "bing": BingProvider(),
            "duckduckgo": DuckDuckGoProvider(),
            "baidu": BaiduProvider(),
            "searxng": SearXNGProvider(),
        }
        raw_pool = cfg.get("pool") or ["bing", "duckduckgo"]
        self.pool = [x for x in raw_pool if x in self.providers] or ["bing", "duckduckgo"]

    def set_pool(self, names):
        valid = [x for x in names if x in self.providers]
        if valid:
            self.pool = valid
            save_config(self.lang, self.pool)
        return valid

    def set_lang(self, lang):
        if lang in ("auto", "zh", "en"):
            self.lang = lang
            save_config(self.lang, self.pool)
            return True
        return False

    def new_topic(self, query):
        self.current_query = query
        self.current_page = 1
        self.all_results = []
        self.lang_code = detect_lang(query) if self.lang == "auto" else self.lang

    def fetch(self, query):
        lang_code = detect_lang(query) if self.lang == "auto" else self.lang
        q = queue.Queue()
        failed = set()

        def worker(name):
            try:
                q.put((name, self.providers[name].search(query, lang_code), None))
            except Exception:
                q.put((name, [], "err"))

        threads = []
        for name in self.pool:
            th = threading.Thread(target=worker, args=(name,), daemon=True)
            th.start()
            threads.append(th)

        collected = []
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                name, res, err = q.get(timeout=0.2)
                if err:
                    failed.add(name)
                else:
                    collected.extend(res)
            except queue.Empty:
                if not any(th.is_alive() for th in threads):
                    break

        cleaned = []
        for r in collected:
            if not r.title or not r.url:
                continue
            if is_ad_or_junk(r.title, r.snippet, r.url):
                continue
            r.score = relevance_score(query, r.title, r.snippet)
            if r.score < 0.12:
                continue
            cleaned.append(r)
        cleaned = dedupe(cleaned)
        return sorted(cleaned, key=lambda x: x.score, reverse=True), sorted(failed)

    def search(self, query):
        if query != self.current_query:
            self.new_topic(query)
        if not self.all_results:
            self.all_results, self.last_failed = self.fetch(query)
        return self.page(self.current_page)

    def page(self, page):
        s = (page - 1) * self.page_size
        e = s + self.page_size
        return self.all_results[s:e]

    def next_page(self):
        self.current_page += 1

    def prev_page(self):
        if self.current_page > 1:
            self.current_page -= 1


def show_results(engine, results):
    start = (engine.current_page - 1) * engine.page_size
    table = Table(title=f"{t(engine.lang, 'results')} | {engine.current_query} | {t(engine.lang, 'current_page')} {engine.current_page}")
    table.add_column("#", justify="right", style="cyan", width=4)
    table.add_column("Title", style="magenta", max_width=42, overflow="fold")
    table.add_column("Snippet", style="white", max_width=62, overflow="fold")
    table.add_column("URL", style="green", max_width=50, overflow="fold")
    table.add_column("Source", style="yellow", width=12)
    for i, r in enumerate(results, start=start + 1):
        table.add_row(str(i), r.title, snippet_preview(r.snippet), r.url, r.source)
    console.print(table)


def main():
    engine = Engine()
    console.print(Panel.fit(
        f"[bold]Terminal Search[/bold]\n"
        f"{t(engine.lang, 'lang_hint').format(lang=engine.lang)} | "
        f"{t(engine.lang, 'pool_hint').format(pool=', '.join(engine.pool))}\n"
        f"[dim]{t(engine.lang, 'supported_pools')}: {', '.join(engine.providers.keys())} | 输入 :help 查看命令[/dim]",
        border_style="cyan"
    ))
    while True:
        try:
            s = console.input(f"[bold cyan]{t(engine.lang, 'prompt')} > [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break
        if not s:
            continue
        if s.startswith(":"):
            parts = s[1:].split()
            cmd = parts[0].lower()
            args = parts[1:]
            if cmd in ("exit", "q"):
                break
            elif cmd == "help":
                console.print(Panel(t(engine.lang, "cmd_help")))
            elif cmd == "lang":
                if args and engine.set_lang(args[0]):
                    console.print(t(engine.lang, "lang_switched").format(lang=engine.lang))
                else:
                    console.print(t(engine.lang, "lang_current").format(lang=engine.lang))
            elif cmd == "pool":
                if args:
                    valid = engine.set_pool(args[0].split(","))
                    if valid:
                        console.print(t(engine.lang, "pool_saved").format(pool=", ".join(engine.pool)))
                    else:
                        console.print(f"{t(engine.lang, 'supported_pools')}: {', '.join(engine.providers.keys())}")
                else:
                    console.print(
                        f"{t(engine.lang, 'supported_pools')}: {', '.join(engine.providers.keys())}\n"
                        f"{t(engine.lang, 'choose_pool')}: {', '.join(engine.pool)}"
                    )
            elif cmd == "new":
                engine.current_query = None
                engine.current_page = 1
                engine.all_results = []
                console.print(t(engine.lang, "new_ready"))
            elif cmd in ("next", "n"):
                engine.next_page()
                if engine.current_query:
                    show_results(engine, engine.page(engine.current_page))
            elif cmd in ("prev", "p"):
                engine.prev_page()
                if engine.current_query:
                    show_results(engine, engine.page(engine.current_page))
            elif cmd == "open":
                if args and engine.current_query:
                    try:
                        idx = int(args[0]) - 1
                        url = engine.all_results[idx].url
                        console.print(f"Opening: {url}")
                        webbrowser.open(url)
                    except Exception:
                        console.print("Invalid number.")
            else:
                console.print("Unknown command.")
        else:
            results = engine.search(s)
            if engine.last_failed:
                console.print(f"[yellow]{t(engine.lang, 'engines_failed')}: {', '.join(engine.last_failed)}[/yellow]")
            if not results:
                console.print(t(engine.lang, "no_results"))
            else:
                show_results(engine, results)


if __name__ == "__main__":
    main()
