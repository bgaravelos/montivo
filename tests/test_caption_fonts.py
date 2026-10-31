"""Greek in every caption font (audit 31/10): Anton, Poppins, DM Serif Display and Montserrat have 0 to 8 of the
Greek letters, so Greek words came out as missing-glyph boxes in four of the five fonts; such a word is drawn in
Inter Tight, which has them all, on the look font's baseline."""
from PIL import Image, ImageChops, ImageDraw

from montivo.engine import graphics as G

GREEK = "αβγδεζηθικλμνξοπρσςτυφχψωάέήίόύώϊϋΐΰΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩΆΈΉΊΌΎΏΪΫ"


def test_every_greek_letter_has_a_glyph_in_the_font_it_is_drawn_in():
    for key in G.FONTS:
        for word in ("Καλημέρα", "όλους", "ΠΡΟΪΟΝ", GREEK, "AI-εργαλεία"):
            k = G.font_key(word, key)
            have = G.charset(G.font_path(k))
            assert all(ord(c) in have for c in word if not c.isspace()), (key, word, k)
        assert G.font_key("Four Meta models, café", key) == key  # Latin keeps the look's font


def test_a_greek_caption_is_not_drawn_in_missing_glyph_boxes(tmp_path):
    for key in G.FONTS:
        look = G.normalize_look({"preset": "marker", "font": key})
        assert look.font == key and not look.box and not look.shadow
        st = G.CaptionStyle.from_look(look)
        w, h = G.draw_caption(["Καλημέρα"], None, [False], st, tmp_path / "c.png")
        got = Image.open(tmp_path / "c.png")
        # the word as the look's own font draws it, where draw_caption puts it: .notdef boxes in four of the fonts
        f = G.font(st.size, key)
        own = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(own).text((st.pad_x + st.stroke - f.getbbox("Καλημέρα")[0], st.pad_y + st.stroke), "Καλημέρα",
                                 font=f, fill=G.rgba(look.text), stroke_width=st.stroke, stroke_fill=G.rgba(look.stroke))
        same = ImageChops.difference(got, own).getbbox() is None
        assert same == (key == "inter-tight"), key  # only Inter Tight draws it itself
        assert got.getchannel("A").getbbox() is not None
        G.draw_callout("Τέσσερα μοντέλα", tmp_path / "k.png", look=look)


def test_the_fallback_word_sits_in_the_look_fonts_line_box():
    for key in G.FONTS:
        main, fb = G.font(84, key), G.fallback_font(84, key)
        assert fb.getmetrics()[0] <= main.getmetrics()[0] and fb.getmetrics()[1] <= main.getmetrics()[1]
        l, t, r, b = G.glyph_box("ψράφω", 84, key)
        assert t >= 0 and b <= sum(main.getmetrics()) and r > l


def test_greek_in_capitals_drops_its_accents():
    st = G.CaptionStyle(upper=True)
    assert [st.shown(w) for w in ("Καλημέρα", "όλους", "προϊόν", "ΐ", "Café")] == [
        "ΚΑΛΗΜΕΡΑ", "ΟΛΟΥΣ", "ΠΡΟΪΟΝ", "Ϊ", "CAFÉ"]
