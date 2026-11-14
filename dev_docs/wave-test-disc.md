# The waveform test disc

Goal: find the rule the CDJ-1000MK3 uses to turn audio into the waveform it records on its SD card (the
50-value `W001` and 100-value `W002` record per track, each value 0 to 15; see
[cdj1000mk3-card.md](cdj1000mk3-card.md), "The waveform"). Reading the firmware did not find it (same file,
"What the firmware says about it"), so this measures it: **one disc of known signals, played once, read back.**

Code: `src/dubcrate/wavetest.py` (the audio and the manifest), `src/dubcrate/wavefit.py` (the analysis),
`spikes/wave_test_disc.py` (`make` and `fit`). The disc's files are made, and checked sample by sample, in
`~/cdj-card/wave-test-disc/` (outside the repository: 63 MB). It can be made again, identically:
`uv run spikes/wave_test_disc.py make ~/cdj-card/wave-test-disc` into a new folder.

## The procedure (the owner, at the hardware, about an hour)

1. **Burn it.** In Dubcrate import `~/cdj-card/wave-test-disc/` (File > Import Folder, and **remove that source
   afterwards**: it is only for this), make a new audio disc, add all 20 tracks **in order** (they sort
   by number), and burn. **In the burn window Normalise must be OFF**: it starts from Settings > Burning, and
   if that is on it would rescale every track and spoil the measurement. Do a **Test Only** burn first if you
   like. A CD-R or a CD-RW will do (the disc is 34 minutes, 20 tracks, gapless). Tracks pass through the burn
   pipeline unchanged: `tests/test_wavetest.py` proves it sample for sample, for FLAC and WAV sources.
2. **Play every track all the way through** on a CDJ-1000MK3 with the SD card in, at normal speed, with the pitch
   at 0 and no jog wheel or search (the player fills a record as it plays; skipped parts stay empty). Start with
   an unplayed disc as far as the card goes: **this disc is new to the card**, so its entry and file are made by
   the player. The tracks are in order of importance, so if time runs out the first ones matter most:
   1, 2, 3 and 4 settle the main scale, whether it follows peak or average, and whether each track is scaled to
   its own loudness. The whole disc is 34 minutes; if the player has a continuous-play mode, use it.
3. **Eject the disc, eject the card from the Mac before pulling it, and copy `PIONEER` off the card** (as for
   `t1-post-cdj`), for example to `~/cdj-card/wave-post-cdj`.
4. `uv run spikes/wave_test_disc.py fit ~/cdj-card/wave-test-disc/manifest.json ~/cdj-card/wave-post-cdj > wave-report.txt`
   and look at it (or give it to the agent). It needs no player and no disc.

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

## How the fit reads it

`wavefit.analyse` regenerates every track from the manifest (so it measures the samples that were burned, not
the nominal levels) and cuts each into the player's columns. Per column it measures six statistics: the louder
channel's peak, the peak of the sum, and RMS and mean absolute value of the sum and of both channels.

- **Alignment**: the shift of the column grid that fits track 3's bursts best (±1 column, in eighths).
- **Which statistic**: the share of column pairs, pooled over the scale tracks, that the player's values put
  the wrong way round for each statistic (0 is a perfect fit). Pooling matters: an RMS player gives equal values
  to equal-RMS shapes, which says nothing alone, but against another track's columns a peak statistic is
  contradicted.
- **The scale**: for the best statistic, the lowest and highest level (dB) seen for each value 0 to 15 from steps
  and ramps, and the boundaries between values.
- **Per segment**: the median value of each segment's columns with its peak and RMS, so the other questions
  (normalisation: track 4 against 1; length: 19 and 20 against 1; frequency; stereo; time constant; the floor) can be
  read straight off.

A value of zero is both "silent" and "not played yet", so an all-zero record counts as not played.
The fitter is tested against a stand-in player with a known rule (`tests/test_wavefit.py`: it recovers a peak
rule and its boundaries, tells an RMS rule from a peak rule, and finds a shifted grid), but **never against a
real player**: expect to adjust it to what the real records look like.

## When the result is in

Put the numbers in [cdj1000mk3-card.md](cdj1000mk3-card.md) (the waveform section and gap 5), and the player's
files in the experiments table. Then waveform pre-writing (a Sync that writes complete records so a disc loads with
its waveforms already drawn) becomes possible: it needs the scale from here and a check that the player accepts a
complete record, which `t2` already showed for an empty file but not for a full one.
