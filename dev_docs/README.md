# Dubcrate developer docs

- [command-line.md](command-line.md): the `dubcrate` command-line tool (import, browse,
  plan discs, burn).
- [burning.md](burning.md): the burn spike, what has and hasn't been tried on real
  hardware, and how to run a hardware check.
- [rekordbox-analysis.md](rekordbox-analysis.md): what we read from rekordbox's database
  and analysis files, and what was measured.
- [cdj1000mk3-card.md](cdj1000mk3-card.md): the CDJ-1000MK3 SD card (cues, loops, hot cues,
  waveform): the format as measured from a real card (the disc ID is the disc's TOC), the
  experiments, `src/dubcrate/cdj_card.py`, and the gaps and where more testing is indicated.
- [card-sync.md](card-sync.md): the plan for a Sync tab that writes cues to the CDJ-1000MK3's card: the
  owner's decisions, how a cue is mapped and merged, the hardware checklist and the phases.
- [wave-test-disc.md](wave-test-disc.md): the disc of test signals that finds the CDJ's waveform scale: how to
  burn and play it, what each of its tracks answers, and how to read what the player wrote (`dubcrate card check-waveforms`).
- [rekordbox-cue-writes.md](rekordbox-cue-writes.md): what rekordbox's `djmdCue` rows look like (measured), the risks of ever
  writing to them, the staged plan, and memory cue editing as built (kept in Dubcrate, never written to rekordbox).
- [audio.md](audio.md): miniaudio and the preview player, what was measured.
- [cross-platform.md](cross-platform.md): Linux and Windows support: what is portable, what
  is macOS-only, the decision to use `cdrdao`, and the phased plan. Read before touching
  burners, tool lookup, paths or packaging.
- [linux-test-plan.md](linux-test-plan.md): the checklist for the first session on a real Linux
  machine (setup, what the container already proved, then the steps that need a display, a sound
  card and a drive).
- [release-readiness.md](release-readiness.md): the README's claims against what has actually been tried, the
  history scan, and suggested README changes, for the first release (draft changelog: `../CHANGELOG.md`).
- [roadmap.md](roadmap.md): what is next, what is unverified, and what to do before
  making the repository public.

Setup and an overview of the code are in [../CONTRIBUTING.md](../CONTRIBUTING.md).
