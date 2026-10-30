---
name: gw-youtube
description: >
  Read YouTube channel stats via CLI — live public counts per video (Data API v3) and the
  YouTube Studio analytics (Analytics API v2): views, engaged views, likes, shares, watch time,
  average percentage viewed, retention curve, traffic sources, subscribers gained, Shorts vs
  long-form — and upload a video (`gw youtube upload`).
  TRIGGERS: "youtube", "youtube stats", "youtube analytics", "youtube studio", "shorts",
  "how are my shorts doing", "video views", "channel views", "audience retention",
  "engaged views", "swiped away", "shorts feed", "upload to youtube", "publish a short"
---

# YouTube

Reads are read-only by construction: the `youtube` opt-in grants `youtube.readonly` and
`yt-analytics.readonly`, which cannot upload, edit or delete anything, and a test asserts it
stays that way. They go through the generic invoker (`gw api call`, see **gw-api**). Uploads
use the separate `youtube-upload` opt-in (see **Upload** below).

## Invocation

```bash
CLI="python3 ${CLAUDE_PLUGIN_ROOT}/cli/gw.py"
```

## Scope prerequisite: an account of its own

```bash
$CLI auth login --account youtube --scopes youtube --email OWNER_GMAIL
```

Google then asks which channel. A channel on a Brand Account has its own identity and the
token belongs to it, so this cannot be an `--add-scopes` on a Gmail account. Check which
channel the token reads before trusting any analytics:

```bash
$CLI api call youtube:v3 channels.list --account youtube \
  --params '{"part":"snippet,statistics","mine":true}' --compact
```

The wrong title means the wrong channel was picked: log in again and pick the other one. The
Cloud project behind the OAuth client needs **YouTube Data API v3** and **YouTube Analytics
API** enabled.

## Live counts (minutes old)

```bash
$CLI api call youtube:v3 videos.list --account youtube \
  --params '{"part":"snippet,statistics,contentDetails","id":"ID1,ID2"}' --compact
```

`statistics` carries `viewCount`, `likeCount` and `commentCount`. These are the public counts,
so any channel's video works. The channel's own uploads: `channels.list` with
`"part":"contentDetails","mine":true` gives `relatedPlaylists.uploads`, then
`playlistItems.list` with `"part":"contentDetails","playlistId":"UU...","maxResults":50`.

## Studio analytics (48–72 h behind)

All are `$CLI api call youtubeAnalytics:v2 reports.query --account youtube --params '{...}'`
with `"ids":"channel==MINE"` and `startDate` / `endDate` as `YYYY-MM-DD`.

| Question | Params besides ids and dates |
|---|---|
| Every Short, best first | `"metrics":"views,engagedViews,likes,comments,shares,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,subscribersGained","dimensions":"video","filters":"creatorContentType==shorts","sort":"-views","maxResults":50` |
| One video by day | `"metrics":"views,engagedViews,likes,averageViewPercentage","dimensions":"day","filters":"video==ID","sort":"day"` |
| Where views came from | `"metrics":"views,engagedViews","dimensions":"insightTrafficSourceType","filters":"video==ID","sort":"-views"` |
| Retention curve (one video per call) | `"metrics":"audienceWatchRatio,relativeRetentionPerformance","dimensions":"elapsedVideoTimeRatio","filters":"video==ID"` |
| Shorts vs long-form | `"metrics":"views,engagedViews,averageViewPercentage","dimensions":"creatorContentType"` |

## Upload

Add the write opt-in to the same account once (pick the same channel again):

```bash
$CLI auth login --account youtube --add-scopes youtube-upload
```

`youtube.upload` inserts videos and nothing else: it cannot edit, delete or change the privacy
of any video, including the one it uploaded. Fix metadata or privacy in YouTube Studio.

```bash
$CLI youtube upload short.mp4 --account youtube \
  --title "Title, up to 100 characters" --description-file description.txt \
  --privacy private --publish-at 2026-11-03T15:00:00Z \
  --not-made-for-kids --category-id 28 --tags "ai agents,lab automation"
```

- **`--privacy` has no default**, and the title, description and made-for-kids declaration are
  required (YouTube's Required Minimum Functionality for uploaders).
- **Get the exact post approved first.** An upload publishes under the channel's name. The
  safe pattern is `--privacy private --publish-at TIME`: the video sits private until YouTube
  publishes it at that time, and Studio can still change or cancel it.
- **A vertical video of 3 minutes or less becomes a Short** automatically; nothing to set.
- **Read the privacy back after processing.** Google's docs disagree on whether uploads from an
  unaudited API project get locked to private. The 2020 changelog says projects created after
  2020-07-28 do, until they pass the
  [compliance audit](https://support.google.com/youtube/contact/yt_api_form); the current
  `videos.insert` reference says they don't. The output's `check_after_processing` line gives
  the `videos.list` call. `private` where you asked for `public` or `unlisted` means locked.
- **Quota:** the `videos.insert` reference lists a default of 100 uploads a day per project.

## Gotchas

- **`creatorContentType` values are camelCase**: `shorts`, `videoOnDemand`, `liveStream`,
  `story`. The uppercase `SHORTS` in Google's dimension reference returns 400 "Invalid value".
- **The lag is real.** A video posted today has no analytics rows for two to three days, and an
  `endDate` past the last processed day just returns fewer rows. Use `videos.list` for today.
- **`views` counts every start and replay; `engagedViews` is the stricter count** (the old Shorts
  view). Compare `engagedViews` over time, not `views`.
- **Traffic source `SHORTS`** means the viewer swiped in from the Shorts feed; `YT_CHANNEL` is
  the channel page.
- **Retention comes back empty** until a video has enough views for YouTube to report it.
- **`subscribersGained` filtered to one video** counts only subscriptions from that video's
  watch page.
- **Not in any API:** Studio's "Viewed vs. swiped away" and Shorts feed impressions. They live
  only in YouTube Studio → Analytics → Content → Shorts.
