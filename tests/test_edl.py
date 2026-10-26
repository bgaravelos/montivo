from montivo.engine.edl import locate, norm_tokens
from montivo.engine.voice import Word


def words(text, t0=10.0):
    return [Word(w=w, t_start=round(t0 + i * 0.3, 3), t_end=round(t0 + (i + 1) * 0.3, 3), turn_id=1, speaker="B", i=i)
            for i, w in enumerate(text.split())]


def test_exact_match_ignores_punctuation_and_case():
    tw = words("You know it's been interesting as we've we've kind of gone through the the pandemic")
    a, b, exact, ratio = locate("it's been interesting, as we've we've kind of", tw)
    assert (a, b, exact, ratio) == (2, 9, True, 1.0)


def test_fuzzy_match_when_model_cleaned_the_quote():
    tw = words("You know it's been interesting as we've we've kind of gone through the the pandemic")
    a, b, exact, ratio = locate("it's been interesting as we've kind of gone through the pandemic", tw)
    assert exact is False and a == 2 and b >= 12 and ratio > 0.8


def test_norm_tokens():
    assert norm_tokens("Hi, Robert. Can you hear me?") == ["hi", "robert", "can", "you", "hear", "me"]
