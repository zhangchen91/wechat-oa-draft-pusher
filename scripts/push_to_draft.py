#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wechat-oa-draft-pusher —— 把文章推送进微信公众号草稿箱。

特性：
- 纯标准库实现（urllib / json / re），无需 pip 安装任何依赖。
- 支持 Markdown(.md) / HTML(.html) / Word(.docx) / 纯文本(.txt) 四种正文。
- 自动上传封面图与正文中所有本地配图，拿到微信永久素材 media_id / mmbiz URL。
- 自动处理 40164（IP 白名单）错误，回显需要加入白名单的出口 IP。
- access_token 本地缓存，避免重复请求。

使用：
  python3 push_to_draft.py \
      --appid wxXXXXXXXX --appsecret xxxxxxxx \
      --article /path/to/article.md \
      [--cover /path/to/cover.jpg] \
      [--images-dir /path/to/images] \
      [--title "标题"] [--author "作者"] [--digest "摘要"] \
      [--token-cache /path/to/token.json] \
      [--config /path/to/config.json] \
      [--no-force-ipv4] [--dry-run]

凭据三种提供方式（优先级：命令行 > config.json > 环境变量）：
  1) 命令行：--appid / --appsecret
  2) 配置文件：--config 指定，或默认读取 <skill>/config.json，内容 {"appid":..., "appsecret":...}
  3) 环境变量：WX_APPID / WX_APPSECRET
（AppSecret 切勿提交进仓库，config.json 已被 .gitignore 忽略。）

网络：默认强制 IPv4 出口。微信 IP 白名单只接受 IPv4，部分环境默认优先 IPv6
（如 2408: 开头）会导致 40164 且无法加白；强制 AF_INET 后微信会看到可加白的 IPv4。

正文中的图片引用（Markdown 的 ![](path) 或 HTML 的 <img src="path">）会被自动识别为本地路径，
上传后替换为微信图床地址。路径相对于文章所在目录解析。
"""

import argparse
import hashlib
import json
import mimetypes
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
ADD_MATERIAL_URL = "https://api.weixin.qq.com/cgi-bin/material/add_material"
DRAFT_ADD_URL = "https://api.weixin.qq.com/cgi-bin/draft/add"
IP_ECHO_URLS = [
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://myip.ipip.net",
]


def log(msg=""):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# 网络层
# --------------------------------------------------------------------------- #
def http_get_json(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def http_post_json(url, payload, timeout=30):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def raise_api_error(data):
    """统一处理微信返回的错误码，遇到 40164 给出白名单 IP 提示。

    优先从微信报错原文里直接解析它「实际看到的调用方 IP」（最权威），
    因为外部回显服务可能从另一个出口出去、给出错误的 IP。
    """
    errcode = data.get("errcode")
    errmsg = str(data.get("errmsg", ""))
    if errcode == 40164 or "40164" in errmsg or "ip" in errmsg.lower():
        m = re.search(r"invalid ip\s+(\d+\.\d+\.\d+\.\d+)", errmsg)
        ip = m.group(1) if m else get_public_ip()
        log("❌ 微信返回 40164：调用 IP 不在公众号 IP 白名单中。")
        if ip:
            log("   请将以下出口 IP（微信实际看到的）加入公众号后台白名单：")
            log(f"   【设置】→【公众号设置】→【IP白名单】→ 添加：{ip}")
        else:
            log("   请在公众号后台【开发】→【基本配置】→【IP白名单】中加入当前服务器出口 IP。")
    else:
        log(f"❌ 微信接口错误 errcode={errcode} errmsg={errmsg}")
    sys.exit(2)


def get_public_ip():
    for url in IP_ECHO_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.read().decode("utf-8").strip()
        except Exception:
            continue
    return None


def get_access_token(appid, appsecret, cache_path=None):
    if cache_path:
        p = Path(cache_path)
        if p.exists():
            try:
                data = json.loads(p.read_text("utf-8"))
                if data.get("access_token") and data.get("expire_at", 0) > time.time() + 300:
                    log("✓ 复用本地缓存的 access_token")
                    return data["access_token"]
            except Exception:
                pass
    url = (
        f"{TOKEN_URL}?grant_type=client_credential"
        f"&appid={urllib.parse.quote(appid)}&secret={urllib.parse.quote(appsecret)}"
    )
    data = http_get_json(url)
    if "access_token" not in data:
        raise_api_error(data)
    log("✓ AppID / AppSecret 验证通过，已获取 access_token")
    if cache_path:
        try:
            data["expire_at"] = int(time.time()) + int(data.get("expires_in", 7200))
            Path(cache_path).write_text(json.dumps(data, ensure_ascii=False), "utf-8")
        except Exception:
            pass
    return data["access_token"]


def upload_permanent_image(access_token, image_path):
    """上传永久图片素材，返回 {"media_id":..., "url":...}。"""
    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"图片不存在: {image_path}")
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    boundary = "----WxDraft" + hashlib.md5(str(path).encode("utf-8")).hexdigest()[:16]
    body = bytearray()
    body += f"--{boundary}\r\n".encode("utf-8")
    body += (
        f'Content-Disposition: form-data; name="media"; filename="{path.name}"\r\n'
    ).encode("utf-8")
    body += f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
    body += path.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")
    url = f"{ADD_MATERIAL_URL}?access_token={access_token}&type=image"
    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "media_id" not in data:
        raise_api_error(data)
    return data


# --------------------------------------------------------------------------- #
# Markdown -> HTML（轻量、无依赖，覆盖公众号常见排版）
# --------------------------------------------------------------------------- #
def _escape(text):
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _inline(text):
    # 图片 ![alt](path) -> 保留本地路径，后续统一上传替换
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: f'<img alt="{m.group(1)}" src="{m.group(2).strip()}">',
        text,
    )
    # 链接 [text](url)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{m.group(2).strip()}">{m.group(1)}</a>',
        text,
    )
    # 行内代码 `code`
    text = re.sub(r"`([^`]+)`", lambda m: f"<code>{_escape(m.group(1))}</code>", text)
    # 粗体 **x** / __x__
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__([^_]+)__", r"<strong>\1</strong>", text)
    # 斜体 *x* / _x_
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<em>\1</em>", text)
    return text


def md_to_html(md):
    lines = md.split("\n")
    out = []
    i, n = 0, len(lines)
    list_type = None
    list_items = []

    def flush_list():
        nonlocal list_type, list_items
        if list_type:
            out.append(f"<{list_type}>")
            for it in list_items:
                out.append(f"<li>{_inline(it)}</li>")
            out.append(f"</{list_type}>")
            list_type = None
            list_items = []

    while i < n:
        line = lines[i]
        s = line.strip()
        if s.startswith("```"):
            flush_list()
            code = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            out.append(f"<pre><code>{_escape(chr(10).join(code))}</code></pre>")
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            flush_list()
            out.append("<hr>")
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush_list()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush_list()
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(lines[i].lstrip()[1:].lstrip())
                i += 1
            out.append(f"<blockquote>{md_to_html(chr(10).join(quote))}</blockquote>")
            continue
        if re.match(r"^\s*[-*+]\s+", line):
            if list_type != "ul":
                flush_list()
                list_type = "ul"
            list_items.append(re.match(r"^\s*[-*+]\s+(.*)$", line).group(1))
            i += 1
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            if list_type != "ol":
                flush_list()
                list_type = "ol"
            list_items.append(re.match(r"^\s*\d+\.\s+(.*)$", line).group(1))
            i += 1
            continue
        if not s:
            flush_list()
            i += 1
            continue
        # 段落，合并到下一个空行或块级元素前
        para = [line]
        i += 1
        while (
            i < n
            and lines[i].strip()
            and not lines[i].lstrip().startswith("#")
            and not lines[i].lstrip().startswith(">")
            and not re.match(r"^\s*[-*+]\s+", lines[i])
            and not re.match(r"^\s*\d+\.\s+", lines[i])
            and not lines[i].strip().startswith("```")
        ):
            para.append(lines[i])
            i += 1
        flush_list()
        out.append(f"<p>{_inline(' '.join(para))}</p>")
    flush_list()
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 正文解析
# --------------------------------------------------------------------------- #
def extract_docx_html(path):
    """尽量从 .docx 抽取 HTML 正文（不依赖外部库，覆盖简单文档）。"""
    try:
        import zipfile
        import xml.etree.ElementTree as ET

        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8")
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        root = ET.fromstring(xml)
        body = root.find(f"{ns}body")
        parts = []
        for p in body.iter(f"{ns}p"):
            texts = []
            for t in p.iter(f"{ns}t"):
                texts.append(t.text or "")
            line = "".join(texts).strip()
            if line:
                parts.append(f"<p>{_escape(line)}</p>")
        return "\n".join(parts)
    except Exception as e:  # noqa
        raise RuntimeError(f"无法解析 .docx（需要含 word/document.xml 的标准文档）: {e}")


def strip_tags(html):
    return re.sub(r"<[^>]+>", "", html).strip()


def parse_article(article_path, base_dir):
    path = Path(article_path)
    suffix = path.suffix.lower()

    # Word 文档是 zip 二进制，单独处理，避免 read_text 解码失败
    if suffix == ".docx":
        html = extract_docx_html(str(path))
        return (path.stem, "", "", html)

    text = path.read_text("utf-8")
    title, author, digest = None, None, None

    # 简单 frontmatter（只支持 title / author / digest 三个字段，无需 yaml 依赖）
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            block = text[3:end]
            for line in block.splitlines():
                m = re.match(r"^\s*(title|author|digest)\s*[:=]\s*(.*)$", line, re.I)
                if m:
                    key, val = m.group(1).lower(), m.group(2).strip().strip('"').strip("'")
                    if key == "title":
                        title = val
                    elif key == "author":
                        author = val
                    elif key == "digest":
                        digest = val
            text = text[end + 4:]

    if suffix == ".md":
        html = md_to_html(text)
    elif suffix in (".html", ".htm"):
        html = text
    else:  # .txt 等纯文本
        html = "\n".join(f"<p>{_escape(l)}</p>" for l in text.splitlines() if l.strip())

    # 自动推断标题
    if not title:
        m = re.search(r"<h1>(.*?)</h1>", html)
        if m:
            title = strip_tags(m.group(1))
        else:
            m = re.search(r"^\s*#\s+(.*)$", text, re.M)
            if m:
                title = m.group(1).strip()
    if not title:
        title = path.stem
    if not digest:
        m = re.search(r"<p>(.*?)</p>", html)
        if m:
            digest = strip_tags(m.group(1))[:120]
    return title, author or "", digest or "", html


def collect_and_upload_images(html, base_dir, access_token, images_dir):
    """扫描 HTML 中的本地图片 src，逐个上传并替换。返回 (new_html, count)。"""
    refs = []
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html):
        refs.append(m.group(1))

    mapping = {}
    uploaded = 0
    for src in dict.fromkeys(refs):  # 去重，保持顺序
        if src.startswith(("http://", "https://", "data:")):
            continue
        # 解析本地路径
        cand = Path(src)
        if not cand.is_absolute():
            cand = (Path(base_dir) / src)
            if images_dir and not cand.exists():
                cand = (Path(images_dir) / Path(src).name)
        if not cand.exists():
            log(f"⚠️  找不到图片，跳过：{src}")
            continue
        try:
            data = upload_permanent_image(access_token, str(cand))
            mapping[src] = data.get("url") or ""
            uploaded += 1
            log(f"  ✓ 已上传配图：{cand.name} -> {data.get('media_id')}")
        except Exception as e:
            log(f"⚠️  上传图片失败，跳过 {src}：{e}")

    for old, new in mapping.items():
        if new:
            html = html.replace(
                f'src="{old}"', f'src="{new}"'
            ).replace(
                f"src='{old}'", f"src='{new}'"
            )
    return html, uploaded


# --------------------------------------------------------------------------- #
# IPv4 强制 + 凭据配置
# --------------------------------------------------------------------------- #
def enable_ipv4_only():
    """强制所有网络请求走 IPv4。

    微信 IP 白名单只接受 IPv4；部分环境默认优先 IPv6（如 2408: 开头），会导致
    40164 且无法加白（白名单字段不收 IPv6）。强制 AF_INET 后，微信会看到可加白的
    IPv4 出口。
    """
    _orig = socket.getaddrinfo

    def _v4(host, port, family=0, type=0, proto=0, flags=0):
        return _orig(host, port, socket.AF_INET, type, proto, flags)

    socket.getaddrinfo = _v4


def load_config_file(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text("utf-8"))
        return {k: v for k, v in data.items() if k in ("appid", "appsecret")}
    except Exception:
        return {}


def resolve_credentials(args):
    """优先级：命令行参数 > 配置文件 > 环境变量。"""
    appid = args.appid or ""
    appsecret = args.appsecret or ""
    if not appid or not appsecret:
        cfg_path = args.config or str(Path(__file__).resolve().parent.parent / "config.json")
        cfg = load_config_file(cfg_path)
        appid = appid or cfg.get("appid") or os.environ.get("WX_APPID", "")
        appsecret = appsecret or cfg.get("appsecret") or os.environ.get("WX_APPSECRET", "")
    return appid.strip(), appsecret.strip()


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="推送文章到微信公众号草稿箱")
    ap.add_argument("--appid", default=None, help="公众号 AppID（也可放 config.json 或用环境变量 WX_APPID）")
    ap.add_argument("--appsecret", default=None, help="公众号 AppSecret（也可放 config.json 或用环境变量 WX_APPSECRET）")
    ap.add_argument("--config", default=None, help="凭据配置文件路径（JSON，含 appid/appsecret），默认读 <skill>/config.json")
    ap.add_argument("--article", required=True, help="文章正文路径 (.md/.html/.docx/.txt)")
    ap.add_argument("--cover", default=None, help="封面图路径（jpg/png）")
    ap.add_argument("--images-dir", default=None, help="配图目录（当正文中用相对文件名引用时）")
    ap.add_argument("--title", default=None, help="文章标题（缺省自动推断）")
    ap.add_argument("--author", default=None, help="作者")
    ap.add_argument("--digest", default=None, help="摘要（缺省取首段）")
    ap.add_argument("--token-cache", default=None, help="access_token 缓存文件路径")
    ap.add_argument("--no-force-ipv4", action="store_true", help="关闭默认的 IPv4 强制（默认强制 IPv4，因微信白名单仅支持 IPv4）")
    ap.add_argument("--dry-run", action="store_true", help="只解析与上传图片，不创建草稿")
    args = ap.parse_args()

    # 默认强制 IPv4 出口：微信 IP 白名单只接受 IPv4，部分环境默认走 IPv6 会导致无法加白
    if not args.no_force_ipv4:
        enable_ipv4_only()
        log("ℹ️  已强制 IPv4 出口（微信白名单仅支持 IPv4）")

    # 解析凭据：命令行 > config.json > 环境变量
    appid, appsecret = resolve_credentials(args)
    if not appid or not appsecret:
        log("❌ 缺少 AppID / AppSecret。请通过以下任一方式提供：")
        log("   1) 命令行：--appid <APPID> --appsecret <APPSECRET>")
        log("   2) 配置文件：在 <skill>/config.json 写入 {\"appid\":..., \"appsecret\":...}")
        log("   3) 环境变量：WX_APPID / WX_APPSECRET")
        sys.exit(1)

    article_path = Path(args.article).resolve()
    base_dir = article_path.parent
    if not article_path.exists():
        log(f"❌ 文章文件不存在：{article_path}")
        sys.exit(1)

    log("=== 1/4 验证 AppID / AppSecret ===")
    token = get_access_token(appid, appsecret, args.token_cache)

    log("=== 2/4 解析正文 ===")
    title, author, digest, html = parse_article(str(article_path), str(base_dir))
    title = args.title or title
    author = args.author or author
    digest = args.digest or digest
    log(f"  标题：{title}")
    log(f"  作者：{author or '(空)'}")
    log(f"  摘要：{digest or '(空)'}")

    log("=== 3/4 上传图片（封面 + 正文配图）===")
    cover_media_id = None
    if args.cover:
        data = upload_permanent_image(token, args.cover)
        cover_media_id = data.get("media_id")
        log(f"  ✓ 封面已上传：{Path(args.cover).name} -> {cover_media_id}")
    else:
        log("  ℹ️  未提供封面，将使用微信默认封面（建议提供 --cover）。")

    html, n = collect_and_upload_images(html, str(base_dir), token, args.images_dir)
    log(f"  ✓ 正文配图上传完成：{n} 张")

    if args.dry_run:
        log("=== DRY-RUN 结束（未创建草稿）===")
        sys.exit(0)

    log("=== 4/4 创建草稿 ===")
    article = {
        "title": title,
        "author": author,
        "digest": digest,
        "content": html,
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
    }
    if cover_media_id:
        article["thumb_media_id"] = cover_media_id
    payload = {"articles": [article]}
    url = f"{DRAFT_ADD_URL}?access_token={token}"
    try:
        resp = http_post_json(url, payload)
    except urllib.error.HTTPError as e:
        try:
            resp = json.loads(e.read().decode("utf-8"))
        except Exception:
            resp = {"errcode": e.code, "errmsg": str(e)}
        raise_api_error(resp)
    if "media_id" not in resp:
        raise_api_error(resp)
    log("✅ 草稿创建成功！")
    log(f"   草稿 media_id：{resp['media_id']}")
    log("   请到公众号后台【草稿箱】检查标题、封面与排版，确认无误后手动点击群发。")


if __name__ == "__main__":
    main()
