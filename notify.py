"""ポケカ抽選情報を監視して、新着と締切間近をDiscordに通知する。

情報源:
  - ポケカ抽選図鑑 (pokeca-navi.jp)      個人店・Xリポスト系に強い
  - 入荷Now (nyuka-now.com)              大手チェーン・アプリ抽選に強い
  - ポケモンカード公式のお知らせ          ポケセンオンラインの抽選告知

環境変数 DISCORD_WEBHOOK_URL が無い場合は送信せず、標準出力に表示する。
"""

import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).parent
CONFIG_PATH = BASE / "config.json"
STATE_PATH = BASE / "state.json"

JST = timezone(timedelta(hours=9))
USER_AGENT = "pokeca-lottery-notify/1.0 (personal use; +https://github.com/grifflet365)"
TIMEOUT = 30

NAVI_URL = "https://pokeca-navi.jp/lotteries/"
NYUKA_URL = "https://nyuka-now.com/archives/2459"
OFFICIAL_URL = "https://www.pokemon-card.com/info/"

# 入荷Nowの記事のうち、通知対象にする見出し(h2)
NYUKA_SECTIONS = {
    "抽選・予約応募受付中のストア": "受付中",
    "近日受付開始予定のストア": "近日開始",
    "【会員限定】抽選販売情報": "会員限定",
}

COLORS = {"new": 0x2ECC71, "remind": 0xE67E22, "warn": 0xE74C3C}


def fetch(url):
    res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    res.raise_for_status()
    res.encoding = "utf-8"
    return BeautifulSoup(res.text, "html.parser")


def make_id(*parts):
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def parse_deadline(text, now):
    """「10/4(日)」「10月8日(木)23:59」などを締切日時にする。時刻が無ければ23:59扱い。"""
    if not text:
        return None
    m = re.search(r"(\d{1,2})\s*[/月]\s*(\d{1,2})", text)
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    t = re.search(r"(\d{1,2}):(\d{2})", text[m.end():])
    hour, minute = (int(t.group(1)), int(t.group(2))) if t else (23, 59)
    try:
        dt = datetime(now.year, month, day, hour % 24, minute, tzinfo=JST)
    except ValueError:
        return None
    # 年が書かれていないので、半年以上過去になる場合は翌年とみなす
    if dt < now - timedelta(days=180):
        dt = dt.replace(year=now.year + 1)
    return dt


def scrape_navi(now):
    soup = fetch(NAVI_URL)
    items = []
    for card in soup.select('[data-slot="card"]'):
        shop = card.select_one(".lottery-card__shop")
        link = card.select_one("a.lottery-card__apply-button")
        if not shop or not link or not link.get("href"):
            continue

        def text(selector):
            el = card.select_one(selector)
            return el.get_text(" ", strip=True) if el else ""

        deadline_el = card.find(
            "div", class_=re.compile(r"^lottery-card__deadline")
        )
        deadline_text = (
            deadline_el.get_text("", strip=True).replace("まで", "") if deadline_el else ""
        )
        url = link["href"]
        items.append({
            "id": make_id("navi", url),
            "source": "抽選図鑑",
            "shop": shop.get_text(strip=True),
            "product": text(".lottery-card__product"),
            "area": text(".lottery-card__area-pill"),
            "method": text(".lottery-card__method-pill"),
            "status": "受付中",
            "deadline_text": deadline_text,
            "deadline": parse_deadline(deadline_text, now),
            "detail": text(".lottery-card__application-summary-text"),
            "url": url,
        })
    return items


def scrape_nyuka(now):
    soup = fetch(NYUKA_URL)
    body = soup.select_one(".postContents")
    if body is None:
        return []
    items = []
    status = None
    for el in body.find_all(["h2", "h3"]):
        if el.name == "h2":
            status = NYUKA_SECTIONS.get(el.get_text(strip=True))
            continue
        if status is None:
            continue
        # h3(店名)の次のh2/h3までにある最初の表が、その抽選の詳細
        table = None
        for sib in el.find_all_next(["h2", "h3", "table"]):
            if sib.name == "table":
                table = sib
            break
        if table is None:
            continue
        rows = {}
        links = {}
        for tr in table.find_all("tr"):
            th, td = tr.find("th"), tr.find("td")
            if not th or not td:
                continue
            key = th.get_text(strip=True)
            lis = td.find_all("li")
            rows[key] = (
                "、".join(li.get_text(" ", strip=True) for li in lis)
                if lis else td.get_text(" ", strip=True)
            )
            a = td.find("a", href=True)
            if a:
                links[key] = urljoin(NYUKA_URL, a["href"])
        shop = el.get_text(" ", strip=True)
        product = rows.get("対象商品", "")
        start = rows.get("開始日", "")
        end = rows.get("終了日", "")
        url = links.get("応募ページ") or links.get("詳細ページ") or links.get("対象商品") or NYUKA_URL
        detail = " / ".join(
            rows[k] for k in ("必須条件", "応募条件", "特記事項") if rows.get(k)
        )
        items.append({
            "id": make_id("nyuka", shop, product, start),
            "source": "入荷Now",
            "shop": shop,
            "product": product,
            "area": "",
            "method": rows.get("抽選形式") or rows.get("販売形式", ""),
            "status": status,
            "start_text": start,
            "deadline_text": end,
            "deadline": parse_deadline(end, now),
            "detail": detail,
            "url": url,
        })
    return items


def scrape_official(now, keywords):
    soup = fetch(OFFICIAL_URL)
    tab = soup.select_one("#newsTab_all") or soup
    items = []
    for a in tab.select("a.List_item_inner")[:40]:
        body = a.select_one(".List_body")
        if not body or not a.get("href"):
            continue
        date_el = body.select_one(".Date")
        date = date_el.get_text(strip=True) if date_el else ""
        label_el = body.select_one(".Calendar_Label")
        for extra in (date_el, label_el):
            if extra:
                extra.extract()
        title = body.get_text(" ", strip=True)
        if not any(k in title for k in keywords):
            continue
        url = urljoin(OFFICIAL_URL, a["href"].strip())
        items.append({
            "id": make_id("official", url, title),
            "source": "公式",
            "shop": "ポケモンカード公式のお知らせ",
            "product": title,
            "area": "",
            "method": "",
            "status": date,
            "deadline_text": "",
            "deadline": None,
            "detail": "",
            "url": url,
        })
    return items


def normalize_shop(name):
    name = re.sub(r"[（(].*?[）)]", "", name)
    return re.sub(r"[\s　・/]|各店|一部店舗", "", name)


def passes_filter(item, config):
    text = f"{item['shop']} {item['product']}"
    if any(k in text for k in config.get("exclude_keywords", [])):
        return False
    includes = config.get("product_keywords", [])
    if includes and item["source"] != "公式" and not any(k in item["product"] for k in includes):
        return False
    if item["source"] == "抽選図鑑":
        return item["area"] in config["areas"]
    return True


def drop_cross_source_duplicates(items):
    """抽選図鑑と入荷Nowで同じ店・同じ締切日のものは、抽選図鑑側を残す。"""
    navi_keys = [
        (normalize_shop(i["shop"]), i["deadline"].date())
        for i in items if i["source"] == "抽選図鑑" and i["deadline"]
    ]
    result = []
    for item in items:
        if item["source"] == "入荷Now" and item["deadline"]:
            shop = normalize_shop(item["shop"])
            day = item["deadline"].date()
            if shop and any(d == day and (n.startswith(shop) or shop.startswith(n)) for n, d in navi_keys):
                continue
        result.append(item)
    return result


def build_embed(item, kind):
    title = item["shop"] if item["source"] != "公式" else item["product"]
    if kind == "remind":
        title = f"⏰ 締切間近: {title}"
    lines = []
    if item["source"] != "公式":
        lines.append(f"**{item['product']}**")
    meta = [x for x in (item.get("area"), item.get("method"), item.get("status")) if x]
    if meta:
        lines.append(" / ".join(meta))
    if item.get("start_text"):
        lines.append(f"開始: {item['start_text']}")
    if item.get("deadline_text"):
        lines.append(f"締切: {item['deadline_text']}")
    if item.get("detail"):
        lines.append(item["detail"][:300])
    return {
        "title": title[:250],
        "url": item["url"],
        "description": "\n".join(lines)[:2000],
        "color": COLORS[kind],
        "footer": {"text": item["source"]},
    }


def send(webhook, content, embeds):
    """Discordは1メッセージ10 embedまでなので分割して送る。"""
    if not webhook:
        print(f"\n[DRY RUN] {content}")
        for e in embeds:
            print(f"  - {e['title']} | {e['description'].replace(chr(10), ' | ')[:160]}")
            print(f"    {e['url']}")
        return
    chunks = [embeds[i:i + 10] for i in range(0, len(embeds), 10)] or [[]]
    for n, chunk in enumerate(chunks):
        payload = {"content": content if n == 0 else "", "embeds": chunk}
        for _ in range(3):
            res = requests.post(webhook, json=payload, timeout=TIMEOUT)
            if res.status_code == 429:
                time.sleep(float(res.json().get("retry_after", 2)) + 0.5)
                continue
            res.raise_for_status()
            break
        time.sleep(1)


def main():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    state = (
        json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if STATE_PATH.exists() else {"items": {}, "broken_sources": []}
    )
    seen = state["items"]
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    now = datetime.now(JST)

    sources = {
        "抽選図鑑": lambda: scrape_navi(now),
        "入荷Now": lambda: scrape_nyuka(now),
        "公式": lambda: scrape_official(now, config["official_keywords"]),
    }
    items = []
    broken = []
    for name, scrape in sources.items():
        try:
            found = scrape()
        except Exception as e:  # 1つの情報源が落ちても他は続ける
            print(f"[{name}] 取得失敗: {e}", file=sys.stderr)
            found = None
        # 公式は抽選のお知らせが0件の時期が普通にあるので、0件を異常扱いしない
        if found is None or (not found and name != "公式"):
            broken.append(name)
            continue
        print(f"[{name}] {len(found)}件")
        items.extend(found)

    items = [i for i in items if passes_filter(i, config)]
    items = drop_cross_source_duplicates(items)

    new_items = []
    reminders = []
    remind_within = timedelta(hours=config["remind_hours_before"])
    for item in items:
        entry = seen.get(item["id"])
        if entry is None:
            # 既に締切を過ぎているものは通知しない
            if not item["deadline"] or item["deadline"] > now:
                new_items.append(item)
            entry = seen[item["id"]] = {
                "shop": item["shop"],
                "product": item["product"],
                "first_seen": now.isoformat(timespec="minutes"),
                "reminded": False,
            }
            # 見つけた時点で締切が近いものは、新着通知だけにしてリマインドを重ねない
            if item["deadline"] and item["deadline"] - now <= remind_within:
                entry["reminded"] = True
        elif (
            not entry.get("reminded")
            and item["deadline"]
            and now < item["deadline"] <= now + remind_within
        ):
            reminders.append(item)
            entry["reminded"] = True
        entry["last_seen"] = now.isoformat(timespec="minutes")

    new_items.sort(key=lambda i: i["deadline"] or datetime.max.replace(tzinfo=JST))
    if new_items:
        send(webhook, f"🎴 ポケカ抽選 新着 {len(new_items)}件",
             [build_embed(i, "new") for i in new_items])
    if reminders:
        send(webhook, f"⏰ 締切まで{config['remind_hours_before']}時間以内 {len(reminders)}件",
             [build_embed(i, "remind") for i in reminders])

    # 情報源が壊れたとき(サイト構造の変更など)は、壊れた最初の1回だけ知らせる
    newly_broken = [s for s in broken if s not in state.get("broken_sources", [])]
    if newly_broken:
        send(webhook, f"⚠️ 取得できない情報源があります: {'、'.join(newly_broken)}"
                      "(サイトの構造が変わった可能性)", [])
    state["broken_sources"] = broken

    # 30日以上見かけていないものは記録から消す
    cutoff = (now - timedelta(days=30)).isoformat(timespec="minutes")
    state["items"] = {k: v for k, v in seen.items() if v.get("last_seen", "") >= cutoff}

    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"新着 {len(new_items)}件 / リマインド {len(reminders)}件 / 対象 {len(items)}件")


if __name__ == "__main__":
    main()
