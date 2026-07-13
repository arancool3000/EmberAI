"""Tests for text_tools."""
import text_tools as T


def test_encoding_roundtrips():
    assert T.base64_decode(T.base64_encode("héllo")["result"])["result"] == "héllo"
    assert T.url_unquote(T.url_quote("a b&c=d")["result"])["result"] == "a b&c=d"


def test_json_pretty():
    assert T.json_pretty('{"a":1}')["ok"]
    assert T.json_pretty("{bad}")["ok"] is False


def test_case_and_slug():
    assert T.case_convert("Hello World", "snake")["result"] == "hello_world"
    assert T.case_convert("hello world", "camel")["result"] == "helloWorld"
    assert T.slugify("Hello, World!")["result"] == "hello-world"


def test_text_analysis():
    s = T.text_stats("one two three. four!")
    assert s["words"] == 4 and s["sentences"] == 2
    assert "a@b.com" in T.extract_emails("mail a@b.com here")["emails"]
    assert "https://x.io" in T.extract_urls("see https://x.io ok")["urls"]
    assert T.word_frequency("a a b")["top"][0] == ("a", 2)


def test_line_ops():
    assert T.find_replace("a-a-a", "a", "b")["result"] == "b-b-b"
    assert T.sort_lines("b\na\nc")["result"] == "a\nb\nc"
    assert T.dedupe_lines("x\nx\ny")["result"] == "x\ny"
    assert T.reverse_text("abc")["result"] == "cba"
    assert T.rot13(T.rot13("hello")["result"])["result"] == "hello"


def test_numbers():
    assert T.int_to_roman(2024)["result"] == "MMXXIV"
    assert T.roman_to_int("MMXXIV")["result"] == 2024
    assert T.number_to_words(1234)["result"] == "one thousand two hundred thirty-four"
    assert T.is_prime(17)["is_prime"] is True
    assert T.is_prime(18)["is_prime"] is False
    lo = T.random_int(1, 5)["result"]
    assert 1 <= lo <= 5


def test_colors():
    assert T.hex_to_rgb("#ff8800")["rgb"] == [255, 136, 0]
    assert T.rgb_to_hex(255, 136, 0)["hex"] == "#ff8800"


def test_calculators():
    assert T.days_between("2024-01-01", "2024-01-31")["days"] == 30
    tip = T.tip_calculator(100, 20, 2)
    assert tip["tip"] == 20 and tip["total"] == 120 and tip["per_person"] == 60
    assert T.bmi_calculator(70, 175)["category"] == "normal"


def test_misc():
    assert len(T.uuid4()["uuid"]) == 36
    assert T.random_pick("a,b,c")["pick"] in ("a", "b", "c")
    assert T.lorem_ipsum(5)["result"].endswith(".")


def _run():
    import types
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and isinstance(v, types.FunctionType)]
    ok = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__); ok += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
    print(f"{ok}/{len(fns)} passed")
    return ok == len(fns)


if __name__ == "__main__":
    import sys
    sys.exit(0 if _run() else 1)
