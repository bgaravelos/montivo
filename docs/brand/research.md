# Montivo brand research (26/10/2026)

Starting point for the new Montivo identity. The 17/10 logo (purple folded M with a play triangle) is retired; nothing from it
carries over. This note collects the facts, maps the category and proposes directions to choose from.

## 1. What the brand has to carry

- The product truth in one line: **an editor that watches its own cut**. Spark plans, renders, reviews the render against a rubric
  and re-cuts. The closed loop is what no competitor shows, and it is the scene the 3-minute demo is built around.
- Where it appears before 5/11: the web UI (run viewer and chat), the title card in rendered reels, the README and repo social
  preview, the 3-minute demo video, the Cerebral Valley gallery thumbnail, and the 20 s "AI editor roasts its own cut" clip on X.
- Hard sizes: a favicon at 16 px, a 1080x1920 frame where the mark sits next to burned-in captions, and a dark editor UI.
- Constraint from the hard rules: at run time only Meta models. The static logo is an asset we design, but anything the product
  generates (for example the Muse Image title card) needs the brand written down as prompt-ready rules: colours as hex, type, layout.

## 2. The name

Billy kept "Montivo" knowingly on 26/10, against the strict-judge verdict that suggested a new name (the worry was a judge asking
"what here is new" if the name matched an older editor; that editor and its repo no longer exist).

Other users of the name, none in video or AI:

| Who | What | Overlap risk |
|---|---|---|
| Montivo (App Store, developer Foxsoft) | Turkish personal-finance app | low |
| Montivo.pk | Pakistani apparel retailer | low |
| The Montivo Group | workforce consulting | low |
| montivo.com | European food and drink distributor for Asia, domain since 2018 | low, but .com is gone |

Domains checked by whois and DNS on 26/10: `montivo.com` taken (2018), `montivo.io` taken (2024), `montivo.app` registered (has
nameservers), `montivo.ai` returned "Domain not found" and no nameservers, so it looks free. Confirm at a registrar before relying on it.
No trademark search was done; for a hackathon entry this is enough, for a company it is not.

The name reads as "montage" with a soft Latin ending. That is an asset: the identity can lean on film-editing vocabulary without
explaining itself.

## 3. Category map: how AI video editors look today

Measured from each homepage on 26/10 (headline font from the computed style, colours from the page):

| Product | Background | Accent | Headline type | Register |
|---|---|---|---|---|
| Descript | blush cream `#FFF7FA` / deep plum | red `#F73B3B` | Brett, display serif | warm, editorial |
| Captions | light, pastel gradient | soft gradient | Exposure, serif | craft ("like a professional editor would") |
| OpusClip | black | white on near-black | Geist 600 | dark tech, "viral" |
| Runway | dark, full-bleed media | none | ABC Normal 400 | gallery, cinematic |
| Submagic | white | orange `#FF4F01` | Inter 600 | generic SaaS |
| VEED | white | lime green | SwissNow 500 | clean Swiss grotesk |
| CapCut | white | cyan | Inter SemiBold | mass-market |
| Kapwing | magenta-to-orange gradient | black | Instrument Sans 800 | loud, creator |
| Gling | soft grey gradient | lime | Byrd italic 700 | friendly YouTuber |

What this shows:
- Two camps. Most use a neutral grotesk (Inter, Geist, Swiss-style) with one accent colour. Descript and Captions moved to a display
  serif to say "craft, not a filter app". Nobody sits in between with a typographic idea of their own.
- Colours already claimed: red (Descript), orange (Submagic), lime (VEED, Gling), cyan (CapCut), black (Opus, Runway), magenta-orange
  (Kapwing). Violet and blue-to-purple gradients are the generic "AI" default and also near Meta AI's own blue-violet ring.
- Marks: play triangles, film frames, scissors and abstract blobs everywhere. The retired logo was a play triangle, which is
  part of why it read as generic.
- Unclaimed: nobody's identity is about **reviewing** or **judgement**. Everyone sells speed ("10x faster", "1 long video, 10 viral
  clips"). The editor's craft of marking up a cut (in and out points, grease-pencil notes, timecode, "v2") is unused as a brand language.

## 4. What the 2026 brand writing says

Agency and design-blog opinion, not market data, but consistent:
- The amorphous swirl or blob "AI logo" is now wallpaper; avoid gradients-as-identity.
- Build from **one ownable seed** (a single shape or metaphor) and repeat it, instead of stacking metaphors.
- Break one category convention on purpose and keep the others, so it still reads as the category.
- Expressive type and confident colour are back after years of neutral palettes.
- Test at small sizes: gradients and thin details vanish in favicons.
- Replace superlatives with evidence. For Montivo that is literal: the review panel with timestamps is the evidence.

## 5. Directions

Each one is a seed, not a finished logo. My recommendation is A.

**A. The mark-up (recommended).** The brand speaks like an editor's notes on a cut. Seed: an in/out bracket pair `[ ]` or a
hand-drawn review circle, the mark a reviewer leaves on a frame. Palette: off-white paper, ink near-black, one grease-pencil
colour (a warm red-orange or a marker yellow, chosen to stay clear of Descript's red and Submagic's orange). Type: a sharp grotesk for
the wordmark, a monospace for timecode (`00:12.40`) used everywhere as a graphic element. Why: it is the closed loop made visible.
The demo's key scene (the agent circles its own mistake at 0:12) becomes the brand itself, and every UI screen, title card and X clip
already contains the motif. Risk: needs discipline so the "hand-drawn" part stays crisp at 16 px (the bracket is the favicon, the
circle is for motion).

**B. The splice.** Seed: the cut point, two shapes meeting with a small offset, as on a timeline. An M built from two clip blocks
with a cut between them. Palette: dark UI, one bright accent. Why: instantly "editing", scales well, geometric. Risk: says editing,
not judgement; close to the timeline icons every editor uses.

**C. Second take.** Seed: `v1 → v2`, a version tag, the slate's "take 2". The wordmark carries a small superscript version
number that counts up (Montivo², or a tag that changes per render). Why: memorable, playful, perfect for the "roasts its own cut"
clip. Risk: a gimmick if the product page does not back it up; weaker as a standalone symbol.

Not recommended: an eye or loop symbol (an eye is surveillance, a loop is the AI swirl again), a play triangle, a film reel, or
a violet gradient.

A and C combine well: the bracket as the mark, the version tag as a motion and copy device.

## 6. What to produce, in order

1. Decide direction and the name question (section 7).
2. Logo: symbol, wordmark, lockup; favicon at 16 and 32 px; light and dark versions. SVG in `assets/brand/`.
3. Tokens: 4 to 6 colours with hex values, type pair, spacing; written so the UI CSS and the Muse Image prompt both read them.
4. Title card and end card templates for rendered reels (9:16 and 16:9).
5. Repo social preview (1280x640), README header, gallery thumbnail.
6. Motion: a 1 to 2 s logo sting for the demo video and the X clip.

## 7. Questions for Billy

- Name: keep "Montivo" and rebuild only the identity, or is the name also open?
- Who should the brand speak to first: the hackathon judges (craft, credibility) or creators and small agencies like Nocta (speed,
  personality)?
- UI mood: dark editor (like Premiere, Opus, Runway) or light paper (like Descript, Captions)? Direction A works in both, but the
  choice sets the palette.
- Is there any tie to Nocta Digital's identity, or must Montivo stand on its own?

## Sources

- Name and domains: [App Store listing](https://apps.apple.com/de/app/montivo/id6755939453),
  [Montivo.pk](https://montivo.pk/apps/track), [Tracxn profile](https://tracxn.com/d/companies/montivo/__68XEIwkKjYXek4CA-ty_r-FziVs8UV5uhHaKXLxyYNE),
  [Montivo Group](https://www.montivogroup.com/), [montivo.com](https://www.montivo.com/); whois and dig run locally on 26/10.
- Category: homepages of [Descript](https://www.descript.com), [OpusClip](https://www.opus.pro), [Submagic](https://www.submagic.co),
  [Runway](https://runwayml.com), [Captions](https://www.captions.ai), [Gling](https://www.gling.ai), [VEED](https://www.veed.io),
  [Kapwing](https://www.kapwing.com), [CapCut](https://www.capcut.com), visited 26/10.
- Positioning claims: [Pictory on OpusClip alternatives](https://pictory.ai/blog/opusclip-alternatives),
  [Typito on Submagic alternatives](https://typito.com/blog/submagic-alternative-submagic-ai-review/).
- Trends: [The Branx, best AI brands of 2026](https://thebranx.com/blog/the-best-ai-brands-of-2026-trends-lessons-and-our-top-picks),
  [Design Shack branding trends](https://designshack.net/articles/trends/top-branding-trends/),
  [DesignCrowd AI branding trends](https://blog.designcrowd.com/article/2265/top-ai-branding-trends-in-2026),
  [Ebaq on AI startup logos](https://www.ebaqdesign.com/blog/ai-startups-logos).
- Internal: `docs/notes/hackathon-fresh-research-and-verdict_2026-10-26.md` (naming advice, demo structure),
  `docs/notes/demo-script_2026-10-26.md`.
