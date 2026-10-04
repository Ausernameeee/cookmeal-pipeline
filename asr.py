"""ASR stage: faster-whisper (CPU int8) -> segments.json.

Includes re-segmentation via word timestamps (fixes small-model fragmentation):
split on sentence-end punctuation / long pauses, merge tiny fragments,
split overlong segments. Zero new dependencies beyond faster-whisper.

Usage:
    python asr.py input.wav output.json [--model tiny.en]
"""
import json, re, sys, time, wave
import numpy as np
from faster_whisper import WhisperModel


def read_wav_mono16k(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1, \
            f"unexpected wav format: {w.getframerate()}Hz {w.getnchannels()}ch"
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _merge_fragments(segs, skip_sentence_end=False, max_words=4, max_dur=2.5,
                   direction="forward"):
    """Merge tiny fragments into a neighbor.

    skip_sentence_end=True: leave chunks ending with [.?!…] alone — they are
    complete sentences, not fragments.
    direction="backward": attach the fragment to the previous piece (used after
    comma-splitting, so debris rejoins its own clause instead of gluing two
    sentences together).
    """
    SENT_END = re.compile(r"[.?!…]+$")
    out, i = [], 0
    while i < len(segs):
        s = segs[i]
        tiny = (len(s["text"].split()) < max_words
                and (s["end"] - s["start"]) < max_dur
                and not (skip_sentence_end and SENT_END.search(s["text"])))
        if not tiny:
            out.append(s)
            i += 1
            continue
        if direction == "backward" and out:
            prev = out.pop()
            out.append({"start": prev["start"], "end": s["end"],
                        "text": (prev["text"] + " " + s["text"]).strip()})
            i += 1
        elif i + 1 < len(segs):
            nxt = segs[i + 1]
            out.append({"start": s["start"], "end": nxt["end"],
                        "text": (s["text"] + " " + nxt["text"]).strip()})
            i += 2
        else:
            out.append(s)
            i += 1
    return out


def _split_long(segs, max_words=20):
    """Split wordy segments at clause boundaries (commas/semicolons/colons).

    Only word count triggers a split — a long-in-time but short-in-words
    sentence may be deliberate for fluency, so duration alone never splits.
    If there is no comma to split on, the segment stays as is.
    Timing is distributed proportionally by character length.
    """
    out = []
    for s in segs:
        if len(s["text"].split()) <= max_words:
            out.append(s)
            continue
        parts = re.split(r"(?<=[,;:])\s+", s["text"])
        if len(parts) < 2:
            out.append(s)
            continue
        total = sum(len(p) for p in parts)
        dur = s["end"] - s["start"]
        t = s["start"]
        for p in parts:
            d = dur * len(p) / total
            out.append({"start": round(t, 2), "end": round(t + d, 2),
                        "text": p})
            t += d
    return out


def resegment(words):
    """words: list of (start, end, word) -> list of {start, end, text}.

    Four passes, tuned over real subtitling sessions:
      1. chunk by sentence-end punctuation / long pauses (>1.2s gaps)
      2. merge tiny fragments forward, protecting complete short sentences
      3. split >20-word segments at clause boundaries
      4. sweep up real debris (<3 words, <1s) backward into its own clause
    """
    SENT_END = re.compile(r"[.?!…]+$")
    chunks, cur = [], []
    for i, (st, en, wd) in enumerate(words):
        cur.append((st, en, wd))
        gap = words[i + 1][0] - en if i + 1 < len(words) else 99
        if SENT_END.search(wd) or gap > 1.2:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)

    def chunk_text(c):
        return " ".join(w for _, _, w in c).replace(" '", "'").strip()

    segs = [{"start": c[0][0], "end": c[-1][1], "text": chunk_text(c)}
            for c in chunks]
    segs = _merge_fragments(segs, skip_sentence_end=True)
    segs = _split_long(segs)
    # second pass: only pick up real debris (<1s) left by the split, merging
    # backward so fragments rejoin their own clause; short but readable
    # clause-pieces (e.g. "Using ultrasonic recorders," at 2.2s) survive
    segs = _merge_fragments(segs, max_words=3, max_dur=1.0,
                            direction="backward")
    return [{"start": round(s["start"], 2), "end": round(s["end"], 2),
             "text": s["text"]} for s in segs]


def main(argv):
    if len(argv) < 2 or argv[0] in ("-h", "--help"):
        print("usage: asr.py input.wav output.json [--model tiny.en]",
              file=sys.stderr)
        return 1
    wav_path, out_path = argv[0], argv[1]
    model_id = "tiny.en"
    if "--model" in argv:
        model_id = argv[argv.index("--model") + 1]

    t0 = time.time()
    model = WhisperModel(model_id, device="cpu", compute_type="int8",
                         num_workers=2)
    print(f"[asr] model loaded in {time.time()-t0:.1f}s", flush=True)
    audio = read_wav_mono16k(wav_path)

    t1 = time.time()
    segments, info = model.transcribe(
        audio,
        beam_size=5,
        vad_filter=True,
        vad_parameters=dict(min_silence_duration_ms=500),
        word_timestamps=True,
    )
    words = []
    for s in segments:
        for w in (s.words or []):
            words.append((round(w.start, 2), round(w.end, 2), w.word.strip()))
    if not words:
        sys.exit("[asr] no words transcribed!")
    segs = resegment(words)
    print(f"[asr] {len(words)} words -> {len(segs)} segments "
          f"in {time.time()-t1:.1f}s (audio {info.duration:.0f}s, "
          f"lang {info.language} p={info.language_probability:.2f})",
          flush=True)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(segs, f, ensure_ascii=False, indent=1)
    print(f"[asr] wrote {out_path}, total {time.time()-t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
