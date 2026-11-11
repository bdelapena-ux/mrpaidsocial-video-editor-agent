---
name: motion-effects
description: >-
  Put named on-screen motion effects on a 9:16 talking-head video from ONE JSON spec:
  kinetic hook, gravity drop, giant captions, prompt bar, file drop, storyboard deck,
  jiggle delete, icon strike, title card, NLE timeline, playing grid, film look, reel
  feed, eyedropper grade, grade slider, video card, comment CTA and comment drop, plus
  karaoke captions, an optional "effects in this video" checklist, and synthesized SFX
  (no API spend). Use when someone picks an effects style from STYLES.md ("the 18-effect
  showcase", "hook + CTA", "cinematic"), names any of these effects ("add a prompt bar
  when I ask the question", "do the jiggle delete on the list", "film look on the
  flashback"), asks to "edit it like that effects video", or wants a saved effects style
  applied to new footage. Also for revisions to an effects edit (move/retime/retext an
  effect). Not for a fully branded HyperFrames ad (branded-ad-edit), banner + captions
  only (reel-recut), or overlays on an uncut take (talking-head-image-overlays).
---

# motion-effects — named effects from one spec

The engine is `scripts/fx_engine.py` (Python + numpy + Pillow + ffmpeg; whisper-cli for
word timings). Every effect is a building block with parameters; a **spec** says which
effects run, when, and with what words. Saved styles are spec *recipes* in
`assets/presets/`. The catalog of effects and every parameter is
[references/effects.md](references/effects.md).

Run with the Python that has numpy + Pillow (the pack's `.venv/` or the one named in
MASTER_CONTEXT.md). In Claude Code's sandbox, whisper needs CPU mode (the engine passes
`-ng`) and writes outside the repo need the sandbox off for that command.

## Workflow

1. **Source = an already-cut 1080x1920 video at an integer fps.** If the footage is raw,
   make the base cut first (silence cuts with `reel-recut`'s graphics-free raw cut, or
   `edl-tighten`), then work on the cut. Probe it with ffprobe. Copy it into the project
   folder (`<projects dir>/<slug>/src/`).
2. **Words.** `fx_engine.py transcribe src/base.mp4 src/words.json` prints `time:word`
   pairs. Read them; note whisper mistakes for `captions.fix`.
3. **Find the face before any layout.** `fx_engine.py face src/base.mp4 _qa/face.png 5`
   writes a gridded frame (each square = 100px at full scale). Look at it. Set
   `layout.caption_cy` just below the chin, `layout.zone_cy` at the chest (graphics live
   there), `layout.top_y` on the wall above the head, `layout.checklist_y` beside the head.
   Graphics never cover the face. Defaults (1270 / 1560 / 70 / 300) suit a face at
   y≈390–1180.
4. **Pick the style.** If the user hasn't, show the effects styles from `STYLES.md` and ask.
   Start from the matching preset in `assets/presets/`. It lists the effects in order with
   example text. For a user's own saved style, MASTER_CONTEXT.md § Saved edit styles points
   at their spec.
5. **Write the spec: map the script to effects.** Each effect gets the window of the words
   it illustrates, and its `at` times land on the spoken word. The text on screen is the
   script's own idea, shortened. This creative step is the edit, so read the transcript and choose:
   a question → prompt bar; a list → storyboard deck then jiggle delete; a before/after or
   good/bad → icon strike; "three things" → title card; parts of a whole → NLE timeline;
   a time-jump → film look; "everyone else is…" → reel feed; a change of mood → eyedropper
   grade; the payoff → grade slider; "part 2 / next video" → video card; the ask → comment
   CTA then comment drop. Windows must not overlap. Use 2–4 s per effect, and keep the first 3 s for the hook.
   Any CTA text the speaker did not say is an addition: tell the user.
6. **Stills first.** `fx_engine.py stills spec.json <one time per effect>` → look at
   `_qa/stills-sheet.jpg`. Fix overlaps (face, captions, checklist) before rendering.
7. **Render.** `fx_engine.py render spec.json [--clips]` builds the video, the SFX mix
   (voice + synthesized whooshes/pops/clicks about 12 dB under, loudnorm to `sound.lufs`),
   muxes it, and with `--clips` writes one short clip per effect. It refuses to overwrite:
   bump the output name for each new version (v1, v2…). About 3 minutes per minute of video. For an audio-only change (SFX level, loudness), bump the output name and run `remix` instead: it reuses the rendered picture.
8. **Verify the MP4, not the spec.** Make a 2 fps contact sheet of the output and look at every effect.
   Run blackdetect/freezedetect, then ebur128 (target −15 LUFS, peak < −1 dBTP). Hand off to
   `video-qa` for the full pass when it is a delivery.
9. **Deliver** per the pipeline (review canvas, or the delivery folder in MASTER_CONTEXT.md).
10. **Save the style** when the user likes it: copy the spec next to the media, and add a line to
    MASTER_CONTEXT.md § Saved edit styles (name → spec path → what it is for). If the
    recipe is generic and new, add an anonymised preset to `assets/presets/` and a row to
    `STYLES.md` (no names, handles or scripts).

## Revisions

Edit the spec (move `start`/`end`/`at`, change text) and render to a new version name.
Screenshot the noted frame before and after (`stills` at the note's time on both specs).

## Rules learned

- The face is the no-go zone. Wall above the head = titles; chest = cards; beside the
  head = checklist. Check `face` output for every new framing.
- Effects that own the words (kinetic hook, gravity drop, giant captions, the title card's
  full-screen half) hide karaoke captions automatically, so don't double the text.
- One effect at a time reads best. If two must overlap, keep one of them a grade (film look,
  eyedropper, slider).
- Synthesized SFX cost nothing. ElevenLabs SFX (`sound-design`) are an upgrade that spends
  credits: ask first.
