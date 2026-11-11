# Edit styles: the menu

Drop a video into Claude Code and say **"edit this"**. Claude will show you this menu and
ask which style to use. You can also name one directly: *"edit this in the explainer
style"*. Each style is a saved recipe, so you never repeat the setup steps.

Your own saved styles (your brand colours, your past edits you liked) are listed in
`MASTER_CONTEXT.md` → **Saved edit styles**. Say *"use my <name> style"*.

## Effects styles (skill: `motion-effects`)

Built from 18 named effects ([catalog](.claude/skills/motion-effects/references/effects.md)).
Each recipe is a JSON file in `.claude/skills/motion-effects/assets/presets/`.

| Say | What you get | Recipe |
|---|---|---|
| **"18-effect showcase"** | Every effect in a row with an "effects in this video" checklist on screen. Shows off range; good for a "look what AI can edit" post. | `showcase-18.json` |
| **"hook + CTA"** | Light touch for everyday reels: a bold slam-in hook, giant words on the key lines, karaoke captions, and a comment-keyword ending. | `hook-and-cta.json` |
| **"explainer"** | Teaching a concept: hook, a chat-style question bar, list cards that wobble and get deleted, an editing timeline for the parts, a red strike on the mistake, a "part 2" card and a comment CTA. | `explainer.json` |
| **"cinematic"** | Story or lesson with mood: letters dropping in, a full-screen chapter card, a film-look flashback, an eyedropper colour regrade, and a before/after slider on the payoff. | `cinematic.json` |
| **"pick effects"** | Name the effects you want and where ("prompt bar on the question, film look on the flashback"). | built from the catalog |

## Other styles (one skill each)

| Say | What you get | Skill |
|---|---|---|
| **"my reel style"** / "banner + captions" | Persistent title banner, word-by-word captions, callout boxes, tight pause cuts. | `reel-recut` |
| **"branded ad"** | Full motion-graphics ad: a designed graphic on every line, brand colours/logo, website B-roll, SFX + music, hook A/B variants. | `branded-ad-edit` |
| **"image overlays"** | One uncut take with pictures and cards popping up on key words, plus a CTA banner. | `talking-head-image-overlays` |
| **"recap"** | Event / trip / day-in-the-life recap from your takes plus B-roll, split screens, captions. | `recap-video` |
| **"edit it like this reel" + a link** | Claude studies the reference frame by frame and copies its style. | `reel-style-clone` → a build skill |
| **"split these hooks"** | One long recording of many hooks becomes one clean video per hook. | `hook-splitter` |
| **"add these hooks to this body"** | One finished body with many openers becomes one video per hook. | `hook-variations` |
| **"Instagram text hook on these"** | Native Instagram Edits text made once and put on many videos. | `edits-hook-overlay` |

## Saving a new style

When you like an edit, say **"save this as my <name> style"**. Claude stores the spec with
the project, adds it to `MASTER_CONTEXT.md` → Saved edit styles, and (if it is a new
generic recipe with no personal details) adds it to the table above so it reaches GitHub.
