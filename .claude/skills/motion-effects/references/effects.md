# Effects catalog + spec format

## Spec (top level)

```json
{
  "source": "src/base.mp4",          // 1080x1920, integer fps, already cut; paths relative to the spec
  "audio": "src/dialogue.wav",       // optional; default = source audio
  "words": "src/words.json",         // [{w,s,e}] from `fx_engine.py transcribe`
  "output": "out/<name>_v1.mp4",     // never overwritten: bump the version
  "brand": {"primary": "#hex", "accent": "#hex", "light": "#hex", "dark": "#hex",
            "font_bold": "path/to/Display-Bold.ttf", "font_serif": "...", "font_serif_italic": "...",
            "handle": "@account"},
  "layout": {"caption_cy": 1270, "zone_cy": 1560, "top_y": 70, "checklist_y": 300},
  "captions": {"enabled": true, "off": [[a, b]], "fix": {"wrong": "right"}},
  "checklist": {"enabled": true, "title": "EFFECTS IN THIS VIDEO", "footer": "18 effects · 52 seconds"},
  "sound": {"sfx": true, "sfx_db": -12, "lufs": -15},
  "effects": [ {"type": "...", "start": 0.0, "end": 2.7, "label": "optional checklist name", ...} ]
}
```

Colours in effect params take `primary | accent | light | dark | white | red | green |
blue | coral | grey | slate | #hex`. Times are seconds on the source timeline. The last
effect may use `"end": null` (= end of video). Omit `checklist` for a normal (non-showcase) edit.

## The 18 effects

| type | what it looks like | script cue | params (★ = required) |
|---|---|---|---|
| `kinetic_hook` | Bold words slam in line by line with a scale-down shake; small serif intro top-left | the opening claim | ★`lines:[{text, at, size=140, color=white}]`, `intro:[str]` |
| `gravity_drop` | Serif letters fall from above and bounce into place, then fall away | a short phrase to land hard ("BREAK EVEN.") | ★`groups:[{text, at, size=150, stagger=.045, dur=.5, exit?}]`, `lead:{text, at}` (italic accent line above) |
| `giant_captions` | One huge word on a plate + italic accent line under it, swapping per beat | 2–4 key words in a row | ★`items:[{word, sub?, at, size=170}]` |
| `prompt_bar` | Chat input types a question, send button pulses, answer bubble pops | a question the speaker answers | ★`text`, `answer`, `label="ASK"`, `type_at`, `type_dur`, `send_at`, `answer_at` |
| `file_drop` | Dashed drop zone; a file chip falls in; zone message changes; chip bar drains | "everything goes into…", "we dump X into Y" | ★`zone_text`, ★`filename`, `zone_text_after`, `sub`, `sub_after`, `tag="CSV"`, `drop_at`, `after_at`, `drain=true`, `drain_at`, `drain_to=.78` |
| `storyboard` | Polaroid cards with frames from the video, each labelled, tilt in one by one | a spoken list | ★`cards:[{label, at, frame_t?, rot?}]` (≤5), `header` |
| `jiggle_delete` | App-icon tiles wobble like iOS edit mode, then get deleted one by one | "cut all of that", "you name it" after a list | ★`icons:[{label, glyph?, color?}]`, `delete_at`, `delete_gap=.2` |
| `icon_strike` | Big arrow tiles pop in; a red strike draws through one; italic word on the wall | good vs bad, did vs forgot | ★`icons:[{label, dir:up/down, at, color?, strike_at?}]`, `word:{text, at}` |
| `title_card` | Full-screen brand card types a title, then the video shrinks into a phone with a scrubber and grows back | "three things…", a chapter | ★`lines:[str]`, `sub:{text, at}`, `small_title`, `phone_at`, `expand_at` |
| `nle_timeline` | Editing-timeline panel; labelled tracks fill as each word is said; playhead sweeps | parts of a whole, a sequence | ★`tracks:[{label, at, from=0, to=1, color?}]`, `title="Timeline"` |
| `playing_grid` | Live video tiles (delayed copies, b/w and warm) fly in with labels | comparing moments / options | ★`tiles:[{label, at, lag?, look:norm/bw/warm}]` (≤3) |
| `film_look` | Warm film grade, grain, weave, flicker, scratch, light leak + mono label | time jump, memory, "end of the month" | `label`, `sublabel` |
| `reel_feed` | Whip-pan into a fake feed post (confetti title, feed UI), then swipe up back to the speaker with feed UI | "everyone else posts…", celebrating, comparison to others | ★`post:{title1, title2, sub, handle, caption, likes}`, `back_at`, `own:{handle, caption, likes}` |
| `eyedropper_grade` | Palette panel samples the frame, pipette clicks the wall then a target swatch, frame regrades | a mood/topic shift | `target="#5f9e6e"`, `target_label`, `pick_at`, `grade_at`, `sample_points` |
| `grade_slider` | Before/after wipe line sweeps across with a handle | the payoff, "now look at it" | `before`, `after` |
| `video_card` | Card with a live thumbnail, play button, progress, title + meta | "part 2", "my other video" | ★`title`, `meta`, `thumb_y=330` (top of the 16:9 crop) |
| `comment_cta` | Comment box types a keyword, Post activates; keyword slams on the wall; CTA line | the ask | ★`keyword`, `line`, `type_at`, `slam_at`, `placeholder` |
| `comment_drop` | The comment drops in with a bounce, gets a heart, the creator's reply appears | right after comment_cta | ★`keyword`, `like_at`, `reply:{text, at, name?}`, `who`, `line`, `keep_slam=true` |

## Layout zones (1080x1920)

- `top_y`: wall above the head. Intro text, `forgot.`-style words, the keyword slam, film labels.
- `zone_cy`: centre of the chest zone (about ±250 px). Every card, panel, tile and icon row.
- `caption_cy`: karaoke captions. Keep it between the chin and the top of the zone.
- `checklist_y`: top of the checklist on the right-hand wall (x ≥ 800).
