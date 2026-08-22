#!/usr/bin/env python3
"""X 推文监测 → WxPusher 推送（云端版，供 GitHub Actions 使用）

环境变量：
  TWITTER_SCREEN_NAME  要监测的 X 用户名（默认 WallStreet0Name）
  WXPUSHER_APP_TOKEN   WxPusher 应用 token（必填）
  WXPUSHER_UID         你的微信 UID（必填）
  RSS_URLS             逗号分隔的 RSS 源（默认 rsshub.app 对应账号，排除回复/转发）
  MAX_PUSH_PER_RUN     单次最多推送条数（默认 20）
"""

import datetime
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

SCREEN_NAME = os.environ.get("TWITTER_SCREEN_NAME", "WallStreet0Name")
APP_TOKEN = os.environ.get("WXPUSHER_APP_TOKEN", "")
UID = os.environ.get("WXPUSHER_UID", "")
MAX_PUSH = int(os.environ.get("MAX_PUSH_PER_RUN", "20"))
RSS_URLS = [
    u.strip()
    for u in os.environ.get(
        "RSS_URLS",
        f"https://rsshub.app/twitter/user/{SCREEN_NAME}/exclude_replies=true&include_rts=false",
    ).split(",")
    if u.strip()
]
STATE_FILE = "state.json"
LAST_RUN_FILE = "last_run.txt"
USER_AGENT = "Mozilla/5.0 (X-Monitor; GitHub Actions)"


def log(msg):
    print(
        "[{}] {}".format(
            datetime.datetime.now(datetime.timezone.utc).isoformat(), msg
        ),
        flush=True,
    )


def fetch(url, timeout=40):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, */*"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def strip_html(text):
    if not text:
        return ""
    t = re.sub(r"<br\s*/?>", "\n", text)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(t).strip()


def parse_rss(content):
    root = ET.fromstring(content)
    items = []
    for it in root.findall(".//item"):
        def txt(tag):
            el = it.find(tag)
            return el.text or "" if el is not None else ""

        link = txt("link")
        m = re.search(r"/status/(\d+)", link)
        if not m:
            continue
        title = strip_html(txt("title"))
        desc = strip_html(txt("description"))
        items.append(
            {
                "id": m.group(1),
                "link": link,
                "title": title,
                "body": desc or title,
                "pub_date": txt("pubDate"),
            }
        )
    return items


def get_new_items(items, seen):
    new = []
    for it in sorted(items, key=lambda x: int(x["id"])):
        if it["id"] not in seen:
            new.append(it)
            seen.add(it["id"])
    return new


def send_wxpusher(content, summary, url):
    payload = json.dumps(
        {
            "appToken": APP_TOKEN,
            "contentType": 2,
            "content": content,
            "summary": summary,
            "uids": [UID],
            "url": url,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://wxpusher.zjiecode.com/api/send/message",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def fmt_time(rfc822):
    try:
        dt = datetime.datetime.strptime(rfc822, "%a, %d %b %Y %H:%M:%S %z")
        try:
            from zoneinfo import ZoneInfo

            dt = dt.astimezone(ZoneInfo("Asia/Shanghai"))
        except Exception:
            pass
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return rfc822


def main():
    log(f"开始监测 @{SCREEN_NAME}")
    if not APP_TOKEN or not UID:
        log("错误：缺少 WXPUSHER_APP_TOKEN 或 WXPUSHER_UID 环境变量")
        sys.exit(1)

    seen = set()
    first_run = not os.path.exists(STATE_FILE)
    if not first_run:
        try:
            with open(STATE_FILE, encoding="utf-8") as f:
                seen = set(json.load(f).get("seenIds", []))
        except Exception as e:
            log(f"读取 state.json 失败，将重新开始: {e}")

    items = []
    for url in RSS_URLS:
        try:
            content = fetch(url)
            items = parse_rss(content)
            log(f"RSS 成功: {url} → {len(items)} 条")
            break
        except Exception as e:
            log(f"RSS 失败: {url} → {e}")

    if not items:
        log("本轮没有获取到推文")
    else:
        new_items = get_new_items(items, seen)
        if first_run:
            log(f"首次运行：记录 {len(new_items)} 条已有推文，不推送")
        elif new_items:
            for it in new_items[:MAX_PUSH]:
                body = it["body"]
                content = (
                    f"📣 @{SCREEN_NAME} 发了新推文\n\n{body}\n\n"
                    f"🔗 {it['link']}\n🕒 {fmt_time(it['pub_date'])}"
                )
                summary = re.sub(r"\s+", " ", body).strip()
                if len(summary) > 60:
                    summary = summary[:60] + "…"
                try:
                    r = send_wxpusher(content, summary, it["link"])
                    if r.get("code") == 1000:
                        log(f"已推送 #{it['id']}")
                    else:
                        log(f"推送失败 #{it['id']}: code={r.get('code')} msg={r.get('msg')}")
                except Exception as e:
                    log(f"推送出错 #{it['id']}: {e}")
        else:
            log("没有新推文")

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"seenIds": sorted(seen)}, f, ensure_ascii=False, indent=2)
    with open(LAST_RUN_FILE, "w", encoding="utf-8") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())
    log("本轮完成")


if __name__ == "__main__":
    main()
