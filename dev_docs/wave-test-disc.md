# The waveform test disc

**Done: both discs were played on 2026-11-15 and the rule is worked out well enough to write waveforms**
(results in [cdj1000mk3-card.md](cdj1000mk3-card.md), "The waveform, measured"; the model is `src/dubcrate/wave_model.py`). This file is
how the discs were made and played, and how to make and read another: a new kind of music, MP3 discs, a different firmware.

Goal: find the rule the CDJ-1000MK3 uses to turn audio into the waveform it records on its SD card (the
50-value `W001` and 100-value `W002` record per track, each value 0 to 15; see the card notes, "The waveform"). Reading the
firmware did not find it, so this measures it: **a disc of known signals, played once, read back.**

Code: `src/dubcrate/wavetest.py` (the audio and the manifest), `spikes/wave_test_disc.py` (`make` disc 1, `probes` for disc 2's
tracks), and `dubcrate card check-waveforms` (`src/dubcrate/wave_check.py`) to compare what the player wrote with the model. Disc 1's
files are made, and checked sample by sample, in `~/cdj-card/wave-test-disc/` (outside the repository: 63 MB). It can be made
again, identically: `uv run spikes/wave_test_disc.py make ~/cdj-card/wave-test-disc` into a new folder.

## The procedure (the owner, at the hardware, about an hour)

1. **Burn it.** In Dubcrate import `~/cdj-card/wave-test-disc/` (File > Import Folder, and **remove that source
   afterwards**: it is only for this), make a new audio disc, add all 20 tracks **in order** (they sort
   by number), and burn. **In the burn window Normalise must be OFF**: it starts from Settings > Burning, and
   if that is on it would rescale every track and spoil the measurement. Do a **Test Only** burn first if you
   like. A CD-R or a CD-RW will do (the disc is 34 minutes, 20 tracks, gapless). Tracks pass through the burn
   pipeline unchanged: `tests/test_wavetest.py` proves it sample for sample, for FLAC and WAV sources.
   **Or from the command line, in a scratch library** (nothing to remove afterwards, and Normalise is off unless
   you pass `--levels`; tried as a dry run on 2026-11-15, the track ids are 1 to 20 in order):

   ```sh
   dc() { uv run dubcrate --library /tmp/wave.db "$@"; }
   dc import-folder ~/cdj-card/wave-test-disc
   dc disc new "Wave test" --pregap 0
   dc disc add 1 --track $(seq 1 20)
   dc burn 1                       # dry run
   dc burn 1 --yes --simulate      # laser-off test write, disc in the drive
   dc burn 1 --yes                 # the burn (ask first: it uses the disc)
   ```

2. **Play every track all the way through** on a CDJ-1000MK3 with the SD card in, at normal speed, with the pitch
   at 0 and no jog wheel or search (the player fills a record as it plays; skipped parts stay empty). Start with
   an unplayed disc as far as the card goes: **this disc is new to the card**, so its entry and file are made by
   the player. The tracks are in order of importance, so if time runs out the first ones matter most:
   1, 2, 3 and 4 settle the main scale, whether it follows peak or average, and whether each track is scaled to
   its own loudness. The whole disc is 34 minutes; if the player has a continuous-play mode, use it.
3. **Eject the disc, eject the card from the Mac before pulling it, and copy `PIONEER` off the card** (as for
   `t1-post-cdj`), for example to `~/cdj-card/wave-post-cdj`.
4. Compare what the player wrote with the model, track by track: `uv run dubcrate card check-waveforms ~/cdj-card/wave-post-cdj
   --toc "~/cdj-card/CD TOC.txt" --audio DIR` (the disc's `drutil toc`, and a folder holding its tracks, each file named with its
   track number first: `wave-test-disc/`'s FLACs, or an audio CD's `1 Audio Track.aiff` files). It needs no player and no disc.
   The raw columns of any record are `dubcrate card show CARD --waveforms`.

## The tracks

All 100 s unless noted, so a `W001` column is 2 s and a `W002` column is 1 s (track 19 is 200 s, track 20 is 40 s).
A level is the **peak** of a sine in dBFS unless it says RMS. Tonal and noise parts carry one bit of TPDF dither.

| # | What | Question it answers |
|---|---|---|
| 1 | 1 kHz sine staircase, -72 to 0 dBFS in 3 dB steps, 4 s each | the main level scale |
| 2 | 1 kHz sine staircase, 4% to 100% of full scale | linear or logarithmic? |
| 3 | silence with full scale bursts of 2, 1, 0.5, 0.1, 0.02, 0.005 s, and single-sample clicks | where the columns begin; peak or averaged? |
| 4 | track 1's staircase 20 dB quieter (-92 to -20) | is each track scaled to its own loudest part? |
| 5 | a steady -60 dBFS tone | a quiet track all the way: does it reach full height? |
| 6 | equal RMS (-20 dBFS) sine, square, triangle, white and pink noise, 1%, 2% and 5% pulses, 12 s each | peak, RMS or average? |
| 7 | -12 dBFS sine: left only, right only, both, right inverted, left 12 dB down, right 12 dB down; independent noise; the same noise | which channel, or the sum? |
| 8 | sine -12 dBFS at 31.5 Hz to 20 kHz, 8 s each | frequency dependence |
| 9 | 1 kHz sine ramping -80 to 0 dBFS (linear in dB) | every level in turn, no step alignment needed |
| 10 | track 6 at RMS -32 | the same, 12 dB quieter |
| 11 | track 8 at -30 dBFS | the same, quieter |
| 12 | 1 kHz bursts at -6 dBFS, 50% on, every 0.1, 0.2, 0.5, 1, 2, 4 s | how fast does the level rise and fall? |
| 13 | silence, dither only, then sines from -90 to -48 dBFS | the bottom of the scale and the floor |
| 14 | 1 kHz sine ramping 0 to full scale (linear in amplitude) | the linear counterpart of 9 |
| 15 | white noise ramping -80 to -6 dBFS RMS | noise has peaks above its RMS |
| 16 | pink noise staircase, RMS -60 to -12 in 2 dB steps | music-like steps |
| 17 | steady pink noise, RMS -30 | a steady, music-like signal |
| 18 | digital silence | the baseline: what an empty track reads |
| 19 | track 1's staircase over 200 s (8 s steps) | does the scale depend on track length? |
| 20 | track 1's staircase over 40 s (1.6 s steps) | the same, short |

## Result of the first run (2026-11-15)

The player's values are **not** the level of the full signal: they follow a **bass band** (125 to 160 Hz, 12 dB an octave) at **2 dB a
value**, 100 columns a track, low nibble first. Disc 1's tracks are why: a 1 kHz tone reads the floor of 2 at any level, the frequency
sweeps (tracks 8 and 11) give the band's shape, the pulses and noise gave the time window. Disc 2 pinned it (below). Disc 1's own
analysis went through three wrong readings first: see "What we first got wrong" in the card notes.

## Disc 2: probes and real music (built, burned and played 2026-11-15)

Built from what disc 1 showed. **Tracks 1 to 10 are probes** (`wavetest.build_probe_tracks`, 19 minutes, made by
`uv run spikes/wave_test_disc.py probes ~/cdj-card/wave-test-disc-2 --first 1`, manifest beside the FLACs), **tracks 11 to 19 are
real music** from `~/Music/Rekordbox` (read-only; picked by a survey of 150 tracks' bass level, loudness, level drops
and range), 44 minutes. The probes come first so they are played even if time runs out; the whole disc is 63 minutes.

| # | Probe | Question |
|---|---|---|
| 1 | 125 Hz at -6, down 2, 4, 6, 8, 12 and 18 dB for 8 s, back up each time | how big a drop makes the next block read low, and to what |
| 2 | 125 Hz at -6 for 7 s, down 12 dB for 1.5 to 8 s | does the dip depend on how long the low level lasts, or on where a block edge falls (7 s steps shift it) |
| 3 | 125 Hz ladder, -69 to 0 dBFS in 3 dB steps, 4 s each | the level scale in the band the player listens to |
| 4 | tones at 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500 Hz, -12 dBFS, 8 s each | the band's shape in third-octave steps |
| 5 | 125 Hz with and without a loud 1 kHz tone, and 1 kHz alone | is the floor a max, a sum or a gain |
| 6 | 125 Hz on the left only, right only, both, right inverted, unequal | the channel sum or the louder channel |
| 7 | pulse trains, periods 0.05 to 2 s, widths 0.5 to 5 ms | what the two values in a block measure |
| 8 | single 125 Hz events of 0.05 to 3 s in silence, at odd and even seconds | attack and hold: which block and which value an event lands in |
| 9, 10 | 125 Hz staircases over 50 s and 300 s | a block is 1/50 of the track: 1 s and 6 s |

Real tracks (11 to 19): Robbie Doherty, *Find A Way* (breakdowns, 100 dB range); Kettama and Interplanetary Criminal, *Yosemite*
(17 big drops); Chase Astate, *Channel Clear*; Inverse, *Longest Road* (the longest); Flo Rida, *Low* (loudest bass); Stonebank, *By Your
Side* (least bass); Michael Jackson, *P.Y.T.* (most bass for its level); ABBA, *Voulez Vous* (steady, quiet); The Proclaimers, *I'm Gonna Be*.

**To burn it** (a scratch library; the real tracks are symlinks, nothing in the music folder is touched; Normalise is off unless
`--levels` is given): `import-folder` the probe folder and a folder of symlinks to the nine tracks, `disc new "Wave test 2" --pregap 0`,
`disc add 1 --track 1 2 ... 19`, `burn 1`, `burn 1 --yes`. **Play every track all the way through** as before, image the card and copy
`PIONEER`. **The audio of the real tracks is what Dubcrate renders**: `dubcrate export 1 DIR` writes the exact WAVs.

**Played 2026-11-15** (card image `cdj-waveform-test-2.img`, the disc is `CDJ00004`; files in `~/cdj-card/wave-post-cdj-2`). Results are in
[cdj1000mk3-card.md](cdj1000mk3-card.md), "Disc 2: probes and real music": the structure (100 columns, low nibble first, no shift), the
exact scale (2 dB a value: a 125 Hz tone at -6 dBFS reads 15), the bass band (125 to 160 Hz, 12 dB an octave), stereo as a mean, the
averaging window (the first 0.7 of a column, a power mean of about 4), the dip after drops of 6 dB or more (unexplained), and how well
the best model does on the nine real tracks (within one value for 76%).
