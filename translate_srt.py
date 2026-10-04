"""Translate segments via the Gemini API, then build a bilingual SRT.

Auth: set the GEMINI_API_KEY environment variable.
Get a free key at https://aistudio.google.com ("Get API key") —
the free tier is plenty for subtitle translation.

Stdlib only (urllib), no extra dependencies.

Usage:
    python translate_srt.py segments.json segments_zh.json bilingual.srt
        [--from en] [--to zh] [--model gemini-3.5-flash-lite]
        [--batch-size 60]
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-3.5-flash-lite"


def _generate(prompt, model, key, temperature=0.1):
    body = {
        "systemInstruction": {
            "parts": [{"text": "You are a professional subtitle translator."}]
        },
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    url = f"{BASE}/models/{model}:generateContent?key={key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:
        detail = e.read(400).decode("utf-8", errors="replace")
        sys.exit(f"[tr] API error {e.code}: {detail[:300]}")
    except Exception as e:
        sys.exit(f"[tr] request failed: {e}")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (KeyError, IndexError, TypeError):
        sys.exit(f"[tr] unexpected response shape: {json.dumps(data)[:300]}")


def _translate_batch(lines, src, dst, model, key):
    """Translate numbered "N|text" lines, keeping numbering, one API call."""
    prompt = (
        f"Translate each numbered {src} subtitle line into {dst}. "
        f"Keep the exact same numbering and line count. "
        f"Output ONLY lines in the same N|translation format, "
        f"no explanations.\n\n" + "\n".join(lines)
    )
    return _generate(prompt, model, key)


def main(argv):
    if len(argv) < 3 or argv[0] in ("-h", "--help"):
        print("usage: translate_srt.py segments.json segments_zh.json "
              "bilingual.srt [--from en] [--to zh] [--model ID] "
              "[--batch-size 60]", file=sys.stderr)
        return 1
    seg_path, zh_path, srt_path = argv[0], argv[1], argv[2]
    src, dst, model, batch_size = "en", "zh", DEFAULT_MODEL, 60
    i = 3
    while i < len(argv):
        if argv[i] == "--from" and i + 1 < len(argv):
            src = argv[i + 1]; i += 2
        elif argv[i] == "--to" and i + 1 < len(argv):
            dst = argv[i + 1]; i += 2
        elif argv[i] == "--model" and i + 1 < len(argv):
            model = argv[i + 1]; i += 2
        elif argv[i] == "--batch-size" and i + 1 < len(argv):
            batch_size = int(argv[i + 1]); i += 2
        else:
            i += 1

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("[tr] GEMINI_API_KEY is not set "
                 "(free key: https://aistudio.google.com)")

    with open(seg_path, encoding="utf-8") as f:
        segs = json.load(f)

    trans = {}
    numbered = [f"{i+1}|{s['text']}" for i, s in enumerate(segs)]
    for b in range(0, len(numbered), batch_size):
        chunk = numbered[b:b + batch_size]
        print(f"[tr] translating lines {b+1}-{b+len(chunk)} "
              f"of {len(numbered)}...", flush=True)
        out = _translate_batch(chunk, src, dst, model, key)
        for line in out.splitlines():
            if "|" in line:
                n, t = line.split("|", 1)
                if n.strip().isdigit():
                    trans[int(n.strip())] = t.strip()

    missing = [i + 1 for i in range(len(segs)) if i + 1 not in trans]
    if missing:
        print(f"[tr] WARN missing translations for lines: {missing[:10]}")
        for m in missing:
            trans[m] = ""

    for i, s in enumerate(segs):
        s["zh"] = trans[i + 1]
    with open(zh_path, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=1)
    print(f"[tr] wrote {zh_path} ({len(segs)} segs)", flush=True)

    def ts(sec):
        h, rem = divmod(sec, 3600)
        m, sec = divmod(rem, 60)
        return (f"{int(h):02d}:{int(m):02d}:{int(sec):02d},"
                f"{int(round((sec-int(sec))*1000)):03d}")

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, s in enumerate(segs, 1):
            f.write(f"{i}\n{ts(s['start'])} --> {ts(s['end'])}\n"
                    f"{s['text']}\n{s['zh']}\n\n")
    print(f"[tr] wrote {srt_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
