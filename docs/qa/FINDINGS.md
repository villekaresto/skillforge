# QA findings

Aggregate results from the unattended QA runs behind v0.1.2–v0.2.0. Per-source details (titles, channels, URLs) are deliberately not published. Only counts and generic categories appear here.

## Batch 1: 60 everyday sources (v0.1.2)

Categories: business-software tutorials, developer screen recordings, wordless craft videos, business and operations frameworks, podcasts of 1–5 hours, entertainment and reviews (expected rejects), non-English tutorials, videos without captions, shorts, a TikTok clip, and one playlist.

| | |
|---|---|
| Sources forged | 59, plus 1 playlist URL that is now listed rather than ingested |
| Skills | 9 (3 merged from several sources), from 14 skill verdicts (24%) |
| Notes | 45 |
| Expected rejects | 6 of 6 rejected |
| Transcripts | auto captions 35, local Whisper 17, manual 8 |
| Fixes | 22, each with tests |

The worst bugs found:

- Local transcription never ran.
- A playlist URL silently ingested the wrong video.
- A colon in a description produced invalid SKILL.md frontmatter.
- Machine-translated caption tracks were used instead of the original.
- Frames were caught mid-transition.

## Batch 2: 40 sources chosen to break things (v0.2.0)

Categories: livestreams of 2–12 hours, multi-hour courses with chapters, multi-speaker panels, clickbait titles, poor-audio recordings (an old TV broadcast, a video call, a noisy workshop, a deliberately degraded local file), Vimeo links, direct `.mp3` URLs, a synthetic local `.mov`, the same procedure in two languages twice, and software and craft how-tos.

| | |
|---|---|
| Skills | 8 of 40 (20%); 2 of these became skills only after re-ingesting with better frames |
| Notes | 30, filed into topic notes |
| Failed | 2 (Vimeo now requires a login; cookies are opt-in) |
| Transcripts | auto captions 25, manual 3, local Whisper 12 (all through MLX) |
| Caption rate limits after the 30 s retry | 3 of 40 (17 of 60 in batch 1, before the retry existed) |
| Ingest time | 35 min in total; the 5-hour stream took 549 s and the 12-hour stream 423 s |

Fixes from this batch:

- frames-only downloads capped at 480p/30 fps;
- `--section` for one chapter of a long video;
- spare frame slots go to the largest time gaps;
- music-only captions flagged;
- bare media URLs keep the given URL and get their duration from ffprobe;
- a login hint for any site that needs an account;
- the combined dry-run simulates notes appended in the same call;
- one video cannot sit in two files of one run;
- legacy notes can be adopted into topic notes.

## Playlist mode

A real 10-video software-tutorial playlist produced 5 skills, one of them merged from two videos with every step citing both, plus 3 note topics, all in one combined dry-run. The clustering helper proposed exactly the two multi-video clusters; the study step kept one as a skill and split the other into notes because the procedures differ.

## Apple Silicon transcription

On a 10:32 video with the `small` model: mlx-whisper took 8.6 s and faster-whisper (CPU, int8) took 59.7 s, about 7× faster. The two transcripts agree on 93.8% of the word sequence.

## Skill quality

The five best skills from each batch were reviewed skeptically and re-forged under the "Write it tight" rules:

- **Batch 1's skills**, written before the rules existed, went from up to 1,953 words and 20 steps down to at most 897 words and 12 steps.
- **Batch 2's skills** were written under the rules. They already passed lint and only needed semantic fixes: demo values, a second method inside a step, optional branches.
- **All 10** re-forged skills are lint-clean at 551–897 words.

## Remaining weaknesses

1. Whether a step is correct is still unverified prose. The lint checks a skill's shape, not its truth.
2. Evidence is often too weak for a verdict: some sources have no captions, and frames are selected blind. Re-ingesting with better evidence is still manual.
3. Version mismatches and "central to the user's work" were guessed in every run until v0.3 added the user profile.
