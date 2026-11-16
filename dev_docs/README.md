# Dubcrate developer docs

Setup and an overview of the code are in [../CONTRIBUTING.md](../CONTRIBUTING.md); the module-by-module notes and the hard rules are in
[../AGENTS.md](../AGENTS.md); the live to-do list is [../TODO.md](../TODO.md) (open items on top, finished ones under *Completed*).

**How these notes are tagged.** Each says how we know: **[hardware DATE]** or **[measured DATE]** on a real drive, player or library;
**[tests]** covered by the automated tests only; **[not tried]** written but never run; **[notes]** someone else's notes, unchecked.
Update the tag when something is checked. Some files open with a status paragraph: read it before the older text beneath it.

## Using and scripting

- [command-line.md](command-line.md): the `dubcrate` command-line tool (import, browse, plan discs, export, burn, the SD card, rekordbox cue writes).

## Burning, audio and the player

- [burning.md](burning.md): the burn pipeline, what has and hasn't been tried on real hardware, and how to run a hardware check.
- [audio.md](audio.md): miniaudio and the preview player; what was measured (buffer sizes, the garbage collector and the GIL).
- [waveform-60fps.md](waveform-60fps.md): why the zoomed waveform scrolls at 60 FPS, how it was made cheap enough, what was measured, and what is still to judge on a real display.

## rekordbox

- [rekordbox-analysis.md](rekordbox-analysis.md): what we read from rekordbox's database and analysis files, and what was measured.
- [rekordbox-cue-writes.md](rekordbox-cue-writes.md): what rekordbox's `djmdCue` rows look like (measured), the risks of writing to them,
  and memory cues and loops as built (the one thing Dubcrate writes to rekordbox).

## The CDJ-1000MK3's SD card

- [cdj1000mk3-card.md](cdj1000mk3-card.md): the card's format as measured from a real card (a disc's ID is its TOC), the experiments, and the gaps where more testing is indicated.
- [card-sync.md](card-sync.md): the Sync tab: the owner's decisions, how a cue is mapped and merged, waveform sync, formatting a card, importing cues back, the hardware checklist and the phases. Start with its **Handover**.
- [wave-test-disc.md](wave-test-disc.md): the discs of test signals that worked out the CDJ's waveform scale: how to burn and play them, and how to read what the player wrote (`dubcrate card check-waveforms`).

## Other systems

- [cross-platform.md](cross-platform.md): Linux and Windows: what is portable, what is macOS-only, the decision to use `cdrdao`, and the phased plan. Read before touching burners, tool lookup, paths or packaging.
- [linux-test-plan.md](linux-test-plan.md): the checklist for the first session on a real Linux machine.

## Planning and releasing

- [roadmap.md](roadmap.md): what is next, the decisions made and why, known rough edges, and what is unverified.
- [release-readiness.md](release-readiness.md): the README's claims against what has actually been tried, the history scan, and what was changed in the README as a result (the release notes draft is [../CHANGELOG.md](../CHANGELOG.md)).
