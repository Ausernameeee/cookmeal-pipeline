#!/usr/bin/env python3
"""Upload a video to Bilibili via web API using stored cookie.

Reads SESSDATA/bili_jct/DedeUserID from a cookie env file: set
BILIBILI_COOKIE_FILE to its path, or use the default bilibili-mcp
.bilibili_env. The file must contain one line like:
    BILIBILI_COOKIE="SESSDATA=...; bili_jct=...; DedeUserID=..."
Keep it at 600 perms and never commit it.

Flow: preupload -> upos chunk upload -> complete ->
x/vu/web/add submit (JSON body, like the web uploader).

Protocol reference: biliup-rs crates/biliup/src/uploader/line/upos.rs
(preupload params + upos_uri path + X-Upos-Auth header).

Usage:
    python3 bili_upload.py <mp4> --title T --desc-file D --tid TID --tag TAGS --source URL
    python3 bili_upload.py <mp4> ... --skip-upload <filename>   # reuse uploaded file
"""
import argparse
import json
import os
import re
import sys
import time

import requests

# Override with BILIBILI_COOKIE_FILE when using this script elsewhere.
ENV_FILE = os.environ.get(
    "BILIBILI_COOKIE_FILE",
    "/home/hatch/workspace/mcp-servers/bilibili-mcp/.bilibili_env",
)
CHUNK = 4 * 1024 * 1024


def load_cookie():
    if not os.path.exists(ENV_FILE):
        sys.exit(f"[up] cookie file not found: {ENV_FILE}\n"
                 "     set BILIBILI_COOKIE_FILE to your cookie env file")
    with open(ENV_FILE) as f:
        line = [l for l in f if l.startswith("BILIBILI_COOKIE=")][0]
    cookie = line.split("=", 1)[1].strip().strip('"').strip("'")
    parts = dict(p.split("=", 1) for p in re.split(r";\s*", cookie) if "=" in p)
    assert parts.get("SESSDATA") and parts.get("bili_jct") and parts.get("DedeUserID"), \
        "cookie missing SESSDATA/bili_jct/DedeUserID"
    return parts


def make_session(ck):
    sess = requests.Session()
    sess.cookies.set("SESSDATA", ck["SESSDATA"], domain=".bilibili.com")
    sess.cookies.set("bili_jct", ck["bili_jct"], domain=".bilibili.com")
    sess.cookies.set("DedeUserID", ck["DedeUserID"], domain=".bilibili.com")
    sess.headers["User-Agent"] = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0 Safari/537.36")
    sess.headers["Referer"] = "https://member.bilibili.com/platform/upload/video/frame"
    return sess


def do_upload(sess, mp4_path, name, size):
    # 1. preupload (params must match biliup; auth signature is bound to them)
    r = sess.get("https://member.bilibili.com/preupload",
                 params={"r": "upos", "profile": "ugcupos/bup", "ssl": 0,
                         "version": "2.11.0", "build": 2110000,
                         "name": name, "size": size})
    pre = r.json()
    assert pre.get("OK") == 1, f"preupload failed: {pre}"
    auth = pre["auth"]
    endpoint = pre["endpoint"]
    if endpoint.startswith("//"):
        endpoint = "https:" + endpoint
    upos_uri = pre["upos_uri"]
    biz_id = pre["biz_id"]
    chunk_size = int(pre.get("chunk_size") or CHUNK)
    url = f"{endpoint}/{upos_uri.replace('upos://', '')}"
    headers = {"X-Upos-Auth": auth}
    print(f"[up] upos url={url[:80]}... chunk={chunk_size}", flush=True)

    # 2. init multipart (POST, empty body)
    r = sess.post(f"{url}?uploads&output=json",
                  headers={**headers, "Content-Length": "0"}, timeout=60)
    up = r.json()
    upload_id = up.get("upload_id")
    assert upload_id, f"uploads init failed: {up}"
    print(f"[up] upload_id={upload_id}", flush=True)

    # 3. chunks (PUT, camelCase params)
    total = (size + chunk_size - 1) // chunk_size
    with open(mp4_path, "rb") as f:
        for i in range(total):
            data = f.read(chunk_size)
            start = i * chunk_size
            params = {"uploadId": upload_id, "chunks": total, "total": size,
                      "chunk": i, "size": len(data), "partNumber": i + 1,
                      "start": start, "end": start + len(data)}
            for attempt in range(5):
                rr = sess.put(url, params=params, headers=headers, data=data,
                              timeout=240)
                if rr.ok:
                    break
                print(f"[up] chunk {i+1}/{total} retry {attempt+1}: "
                      f"{rr.status_code} {rr.text[:120]}", flush=True)
                time.sleep(5)
            else:
                sys.exit(f"[up] chunk {i+1} failed after retries")
            print(f"[up] chunk {i+1}/{total} ok", flush=True)

    # 4. complete (POST, JSON body)
    parts = [{"partNumber": i + 1, "eTag": "etag"} for i in range(total)]
    r = sess.post(url, headers=headers,
                  params={"name": name, "uploadId": upload_id, "biz_id": biz_id,
                          "output": "json", "profile": "ugcupos/bup"},
                  json={"parts": parts}, timeout=60)
    done = r.json()
    assert done.get("OK") == 1, f"complete failed: {done}"
    filename = upos_uri.split("/")[-1].rsplit(".", 1)[0]
    print(f"[up] complete ok, filename={filename}", flush=True)
    return filename


def do_submit(sess, ck, args, filename):
    with open(args.desc_file, encoding="utf-8") as f:
        desc = f.read()
    studio = {
        "copyright": 2,  # 转载
        "source": args.source,
        "tid": args.tid,
        "cover": "",
        "title": args.title,
        "desc_format_id": 0,
        "desc": desc,
        "dynamic": "",
        "tag": args.tag,
        "videos": [{"filename": filename, "title": args.title, "desc": ""}],
        "dtime": None,
        "open_subtitle": False,
        "interactive": 0,
        "dolby": 0,
        "lossless_music": 0,
        "no_reprint": 1,
        "open_elec": 0,
        "up_close_reply": False,
        "up_close_danmaku": False,
        "up_selection_reply": False,
    }
    ts = int(time.time() * 1000)
    r = sess.post(
        f"https://member.bilibili.com/x/vu/web/add?t={ts}&csrf={ck['bili_jct']}",
        json=studio, timeout=60)
    res = r.json()
    print("[up] submit response:", json.dumps(res, ensure_ascii=False)[:600], flush=True)
    if res.get("code") != 0:
        sys.exit(f"[up] submit failed code={res.get('code')}: {res.get('message')}")
    bvid = res["data"]["bvid"]
    print(f"[up] SUCCESS bvid={bvid} https://www.bilibili.com/video/{bvid}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mp4")
    ap.add_argument("--title", required=True)
    ap.add_argument("--desc-file", required=True)
    ap.add_argument("--tid", required=True, type=int)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--skip-upload", default="",
                    help="reuse an already-uploaded filename, skip chunk upload")
    args = ap.parse_args()

    ck = load_cookie()
    sess = make_session(ck)

    if args.skip_upload:
        filename = args.skip_upload
        print(f"[up] reusing uploaded filename={filename}", flush=True)
    else:
        size = os.path.getsize(args.mp4)
        name = os.path.basename(args.mp4)
        print(f"[up] file {name} {size/1048576:.1f} MiB", flush=True)
        filename = do_upload(sess, args.mp4, name, size)

    do_submit(sess, ck, args, filename)


if __name__ == "__main__":
    main()
