# Before the first release: what the README claims, and what was found in the history

Written 2026-11-15 for the owner (`TODO.md`, "Before making the repo public"). The repository is **already public**, so nothing
here changes history: the scan only reports. **Updated 2026-11-16:** the README was brought into line with most of the suggestions below (marked *applied*); the claims table keeps its 2026-11-15 findings, with what has changed noted in the rows.

Status words: **Tried** (done on real hardware or a real library, with the date or the evidence), **Tests only** (the automated tests
cover it, nobody has done it for real), **Not tried**, **Wrong now** (the README no longer matches the app).

## The README's claims

| Claim (README) | Status | Evidence, and what is missing |
| --- | --- | --- |
| Burns audio CDs with CD-TEXT, titles appear on the CDJ | **Tried** (burn path), **not tried** (the burn window by hand) | 2026-11-11: three tones burned through DiscRecording, a CDJ-1000MK3 showed the track times and CD-TEXT, accents included. 2026-11-15: two discs (20 and 19 tracks) burned from the command line, TOC exact. The **burn window** itself ("the burn window, the gapless fix, ... are not proven on hardware or a real display", AGENTS.md), and the window's drive check, have not been used on a real disc |
| Discs are gapless by default | **Tried** (the table of contents), not heard | 2026-11-15: `drutil toc` of two burns shows each track starting exactly where the last ends, and the planned TOC equals the disc's. Needed a fix first (the drive refuses a *required* zero pregap, `burning.md`). Whether a CDJ plays them gaplessly was not noted |
| Plans CDs that fit exactly (74 or 80 minutes) | **Tried**, partly | Lengths are measured and the planned TOCs matched two real discs exactly. The drive reported 79:57.69 (359,844 blocks) free on an 80-minute CD-R. A 74-minute disc and a nearly full disc were **not tried** (`burning.md`) |
| A disc can't be burned if over its size or a file is missing | **Tests only** | |
| Burns **MP3 discs** | **Tests only** | No real MP3 disc has been burned or played (TODO: the load time, file order, folders, 999 files, depth) |
| Imports your rekordbox library, read-only, with playlists | **Tried** | The owner's rekordbox 7 library (about 1,084 tracks), through a snapshot copy; importing never writes to it |
| Memory cues edited in the player are **saved into rekordbox's library** (asked once, Settings > Library) | **Tried** (command line, the owner's live rekordbox 7.2.19 library: "worked perfectly"), **tests only** (the in-app path) | `dubcrate rekordbox write-memory-cues --yes`; the app's `RekordboxEditor` is tested with a stand-in writer. Not checked: a rekordbox restart and a USB export after a write (not itemised), the in-app flow with rekordbox open and closed |
| **Import Cues…** brings memory cues set on the player back from the card | **Tests only** | Built 2026-11-15 for additions; since 2026-11-16 it also brings back loops and the player's hot cues (unticked) and notices cues deleted or moved (against a baseline each sync records). Never tried with cues set on a real player |
| rekordbox 7 supported, 6 "likely" | **Tried** (7), **not tried** (6) | The README says 6 is untested: honest |
| Preview with waveform, rekordbox's own waveform, beat grid, phrases and cues | **Tried**, partly | Checked against real files; the "mid" and "low" phrase names are from notes, not checked; the four looks were rendered offscreen and **not looked at on a real display** (TODO) |
| Burn **speed** adjustable, slower recommended for old players | **Tried** (speeds 10 to 24x offered, 10x used) / **Not measured** (the reliability claim) | The "slower is more reliable on old players" advice is folk wisdom, not something measured here |
| Needs **a CD burner** for CD-R; CD-RW | **Tried** (CD-R, one LiteOn USB burner) / **Not tried** (CD-RW) | All burns so far were CD-R on a LiteOn eSAU108. **Fixed 2026-11-16:** the README said CD-RW "works" while the guide and `burning.md` said CD-R; all now say CD-R, with CD-RW not recommended and not tried |
| ffmpeg is included in the download | **Tried** locally / **Not tried** by CI | 2026-11-13: bundling proven in a local Apple Silicon build. `build.yml` (arm and Intel builds, the smoke test of each) has **never run**: no tag has been pushed (TODO "Run build.yml in CI"). The Intel build needs `nasm` and was not built |
| Download `.dmg` names, "needs macOS 14 or later" | **Names: tests only; macOS 14: not tried** | `test_build_files.py` checks the names against the script and the workflow. The deployment target is 14.0, but the app has only been run on a much newer macOS (this machine's Darwin 25) |
| Not signed; "Open Anyway" steps | **Not tried** | Not opened on a clean Mac (TODO "Open the built .dmg on a clean Mac"). The steps are the usual ones for macOS 14 and later |
| Works offline except the update check | **Tests only** | `conftest.py` blocks the network for every test, and the app's only request is GitHub's release list. The update check has never seen a real release (the repository was private, so it got a 404) |
| Print an index label for the case (⌘P) | **Tried** | Printed on real paper. Not known: whether the Mac print panel keeps the paper size asked for |
| Titles use CD-TEXT, accents kept, other characters simplified or "?" | **Tried** (accents), **Tests only** (the rest) | |
| "Cue points ... are not burned to the CD. An audio CD can't hold them and an old CDJ can't read them anyway" | **Wrong then; fixed 2026-11-16** | Dubcrate writes the cues to the CDJ-1000MK3's **SD card** (Sync), and the player reads them there. The README now says the *disc* doesn't hold them and points to Sync |
| Windows and Linux "planned in the very near future" | **Not tried; reworded 2026-11-16** | Groundwork only (a `cdrdao` burner, drive lists, CI legs, Export, a Linux AppImage built in Docker); none of it has been run on a real Linux or Windows machine. The README now says Linux is built but not yet tried and Windows is planned, with no promise of when |
| Other DJ libraries "coming" | **Not tried** | Mixxx needs a real database to check against; Engine DJ and djay are backups synced from rekordbox, per the owner |
| Badges: Build, Tests, Downloads, Qt 6.12 LTS, Python 3.13+ | **Mixed** | Build shows no status until `build.yml` runs; Tests is now **Linux only** on push (macOS and Windows are run by hand); Downloads is 0 until a release exists; Qt (`>=6.12,<6.13`) and Python (`>=3.13`) match `pyproject.toml` |
| Screenshots | **Staged** | They are made from invented music and a pretend card (`scripts/make_screenshots.py`); the README's captions don't say so |

## What is not in the README but exists

Each of these would be a new claim, so each needs its status said: **Sync of cues** to the SD card (tried on one CDJ-1000MK3 and one card),
**waveforms** to the card (the player accepted, drew and kept records written by Dubcrate; the Sync tab's own waveform writing is untried),
**Format a Card** (untried on a player), **level matching** (tests only, not listened to), the **Explorer** (not tried on slow or unplugged
drives or with macOS's permission prompts), **export** for other burners (tests only).

## Suggested README changes (status 2026-11-16)

1. *(applied)* Replace the "Cue points ..." bullet with: *"**Cue points and beat grids are not burned to the CD**, which can't hold them. For the
   CDJ-1000MK3, Dubcrate can write your rekordbox cues, and the player's waveform for each track, to the player's SD card instead (the
   **Sync** tab), so a disc loads with its cues already set."*
2. *(applied, in two bullets)* Add a bullet under **What it does**: *"Writes your rekordbox cues (and waveforms) to a **CDJ-1000MK3's SD card**, and can format a blank card
   for it. Tried on one player and one card: see the guide."*
3. *(applied)* Fix the CD-RW sentence to say what has been tried: *"Use blank **CD-R** discs (CD-RW is not recommended and has not been tried)."*
4. *(applied)* Soften "very near future" to *"Windows and Linux are planned."*
5. *(applied)* Say once that the screenshots use invented music.
6. *(partly applied: the README links the changelog; the update check's request is already mentioned under Download)* Add the changelog link once a release exists, and a line about the update check's one request.
7. *(still yours)* Re-read the trademark paragraph (TODO): it names Apple, AlphaTheta and Pioneer DJ and says rekordbox, CDJ and Pioneer DJ are trademarks of their owners.

## The history scan (read-only; 99 commits, one author)

Searched every commit's contents and file names, not just the current files.

- **No secrets or keys**: no tokens, passwords, private-key blocks or cloud keys in any commit.
- **No binaries that shouldn't be public**: the largest blob ever committed is a 0.5 MB screenshot. No firmware (`.EXE`, `.UPD`), no audio, no
  databases, no card images. The only non-code data files are seven small CDJ card files (`tests/data/cdj_card/*.PDJ`, the player's own
  cue and waveform files for discs of the owner's) and three `drutil toc` printouts.
- **The author email `martincurkovic@icloud.com`** is on all 99 commits and in `pyproject.toml`. It is public now. GitHub offers a private
  `…@users.noreply.github.com` address for future commits (Settings > Emails); the existing commits can only be changed by rewriting history
  and force-pushing, which this scan does not do.
- **The macOS user name** (`/Users/martincurkovic`) is in two old commits, inside a Python traceback pasted into `TODO.md`
  (81830e2) and one other (54f6e12); not in any current file. It is the same as the GitHub handle.
- **A third party's email**, `andreas@daneb.de`, is in a test fixture: it is cdrdao's own version banner (its author's public address), copied
  from the tool's output. Harmless, but it is someone else's address in your repository.
- **Your private layout and music**, in current files: documentation names about 40 paths like `~/cdj-card/` and `~/Music/Rekordbox`
  (the owner's own folders, described as "the owner's data"), the test discs' nine real tracks by artist and title
  (`wave-test-disc.md`), and three tracks used to confirm rekordbox's cue kinds (`card-sync.md`). Not sensitive, but it is your music taste
  and your folders, in public. `AGENTS.md` and `CLAUDE.md` stay public (the owner's decision, 2026-11-15).

Decisions that are yours: whether to keep that email on the commits, whether to name the tracks and folders in the dev notes, and whether to
write a first-release note that says the card features are new.
