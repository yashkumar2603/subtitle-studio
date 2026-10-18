from app import subs
from app.transcribe import clean_segments, fix_english_casing, split_on_gaps


def cue(start, end, *lines):
    out, t = [], start
    for line in lines:
        ws = []
        for word in line.split():
            ws.append({"t": word, "s": t, "e": t + 0.3})
            t += 0.3
        out.append(ws)
    return {"start": start, "end": end, "lines": out}


def test_play_res_short_side_is_1080():
    assert subs.play_res(1920, 1080) == (1920, 1080)
    assert subs.play_res(1080, 1920) == (1080, 1920)
    assert subs.play_res(3840, 2160) == (1920, 1080)


def test_ass_colors_and_lines():
    ass = subs.build_ass([cue(1, 3, "Hello there", "second line")], {"textColor": "#FF8000"}, 1920, 1080)
    assert "PlayResX: 1920" in ass and "WrapStyle: 2" in ass
    assert "&H000080FF" in ass  # ASS is AABBGGRR
    assert "Hello there\\Nsecond line" in ass


def test_box_adds_layer_and_escapes_braces():
    ass = subs.build_ass([cue(0, 2, "a {b} c")], {"boxEnabled": True}, 1280, 720)
    assert "Style: Box" in ass
    assert ass.count("Dialogue:") == 2
    assert "{b}" not in ass.split("[Events]")[1].replace("{\\", "")


def test_word_highlight_emits_one_event_per_word():
    ass = subs.build_ass([cue(0, 2, "one two three")], {"highlightMode": "word"}, 1920, 1080)
    assert ass.count("Dialogue:") == 3
    assert "\\1c&H" in ass


def test_karaoke_fill_uses_kf():
    ass = subs.build_ass([cue(0, 2, "one two")], {"highlightMode": "fill"}, 1920, 1080)
    assert "\\kf" in ass


def test_srt_vtt():
    cues = [cue(0, 1.5, "hi"), cue(2, 3, "bye")]
    assert subs.build_srt(cues).startswith("1\n00:00:00,000 --> 00:00:01,500\nhi")
    assert "00:00:02.000 --> 00:00:03.000" in subs.build_vtt(cues)


def test_parse_srt_roundtrip():
    segs = subs.parse_srt_vtt(subs.build_srt([cue(0, 1.5, "hello world")]))
    assert segs[0]["text"] == "hello world" and len(segs[0]["words"]) == 2


def test_join_words_cjk():
    assert subs.join_words(["Hello", "world"]) == "Hello world"
    assert subs.join_words(["你好", "世界"]) == "你好世界"


def seg(text, start, end, p=0.95, nsp=0.0, lp=-0.2, cr=1.5):
    words = subs.interpolate_words(text, start, end)
    for w in words:
        w["p"] = p
    return {"start": start, "end": end, "text": text, "words": words, "nsp": nsp, "lp": lp, "cr": cr}


def test_filter_removes_phantoms_keeps_speech():
    raw = [
        seg("Thank you.", 0, 1, p=0.5),  # typical hallucination on music
        seg("So here I am in my new flat.", 10, 12),
        seg("♪", 20, 22),
        seg("[Music]", 30, 32),
        seg("Thank you so much for coming.", 40, 42),  # real speech, confident
        seg("garbled", 50, 51, p=0.2),
    ]
    out, removed = clean_segments(raw, "normal")
    texts = [s["text"] for s in out]
    assert texts == ["So here I am in my new flat.", "Thank you so much for coming."]
    assert removed == 4


def test_filter_off_keeps_everything_but_tags():
    out, _ = clean_segments([seg("Thank you.", 0, 1, p=0.5)], "off")
    assert out[0]["text"] == "Thank you."


def test_repeat_loop_removed():
    raw = [seg("I'm so happy.", i * 3, i * 3 + 1) for i in range(4)]
    out, removed = clean_segments(raw, "normal")
    assert len(out) == 2 and removed == 2


def test_split_on_gaps():
    s = seg("So I have unpacked", 0, 4)
    s["words"][1]["s"] += 30
    s["words"][1]["e"] += 30
    for w in s["words"][2:]:
        w["s"] += 30
        w["e"] += 30
    parts = split_on_gaps([s])
    assert [p["text"] for p in parts] == ["So", "I have unpacked"]


def test_english_casing():
    segs = [{"words": [{"t": w} for w in "so i'm here. yay".split()]}]
    fix_english_casing(segs)
    assert segs[0]["text"] == "So I'm here. Yay"
