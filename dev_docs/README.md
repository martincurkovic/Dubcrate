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
- [audio.md](audio.md): miniaudio and the preview player, what was measured.
- [cross-platform.md](cross-platform.md): Linux and Windows support: what is portable, what
  is macOS-only, the decision to use `cdrdao`, and the phased plan. Read before touching
  burners, tool lookup, paths or packaging.
- [linux-test-plan.md](linux-test-plan.md): the checklist for the first session on a real Linux
  machine (setup, what the container already proved, then the steps that need a display, a sound
  card and a drive).
- [roadmap.md](roadmap.md): what is next, what is unverified, and what to do before
  making the repository public.

Setup and an overview of the code are in [../CONTRIBUTING.md](../CONTRIBUTING.md).
