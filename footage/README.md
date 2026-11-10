# Camera footage

Each ghat's CCTV camera plays the video named after the ghat in this folder:

| Ghat | File (any of `.mp4 .mov .webm .mkv .avi .m4v`) |
|---|---|
| Ram Ghat | `ram-ghat.*` |
| Mangalnath Ghat | `mangalnath-ghat.*` |
| Triveni Ghat | `triveni-ghat.*` |
| Gau Ghat | `gau-ghat.*` |

A camera with no file shows "No footage yet" and an **Add a video…** button on the dashboard; a video added
that way is saved here under the right name. To use a real camera instead of a file, set
`CCTV_SOURCE_<GHAT>` in `.env`, for example `CCTV_SOURCE_RAM_GHAT=rtsp://user:pass@10.0.0.5/stream`.

Videos here are not committed to git (they are large, and most are not ours to redistribute).

## What ships with the project

`./run.sh` downloads a stand-in clip for each camera the first time it runs. They are fixed-camera
crowd scenes used as placeholders for the prototype. **None of them shows the Ujjain ghat it is
attached to**; each camera's head count is still reported against its own ghat.

| Camera | File | What it really shows | Source and licence |
|---|---|---|---|
| Ram Ghat | `ram-ghat.mp4` | Shibuya crossing, Tokyo | [Mixkit 4401](https://mixkit.co/free-stock-video/crowds-of-people-cross-a-street-junction-4401/), [Mixkit Stock Video Free License](https://mixkit.co/license/#videoFree) |
| Mangalnath Ghat | `mangalnath-ghat.webm` | Saint-Lazare station, Paris | "2025-11-06 17-33-16 - Paris - Saint-Lazare" by *Baidax*, [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:2025-11-06_17-33-16_-_Paris_-_Saint-Lazare.webm), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) (480p version, unmodified) |
| Triveni Ghat | `triveni-ghat.webm` | Buchanan Street, Glasgow | "Pedestrians in Buchanan Street, Glasgow", uploaded by *Stinglehammer*, [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Pedestrians_in_Buchanan_Street,_Glasgow.webm), [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/) (480p version, unmodified) |
| Gau Ghat | `gau-ghat.mp4` | Bathers at a ghat on Pushkar Lake, Rajasthan | [Mixkit 6799](https://mixkit.co/free-stock-video/crowd-by-the-river-in-pushkar-lake-6799/), [Mixkit Stock Video Free License](https://mixkit.co/license/#videoFree) |

The Mixkit licence allows use in a project but not redistribution of the clips themselves, which is
one more reason they are downloaded rather than committed.

To use your own footage, delete a camera's file, put yours in its place under the same name
(any listed extension) and restart `./run.sh`.
