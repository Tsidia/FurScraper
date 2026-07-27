# FurScraper

A personal archiver for two furry art sites - [e621](https://e621.net) and [FurAffinity](https://www.furaffinity.net) with a Windows GUI for configuration, a scheduled background runner, and a local web gallery that presents posts from both sources in a single browsable view.

## What it does

- Pulls new posts from configurable e621 tag searches.
- Pulls new submissions from three FurAffinity channels: specific artists (gallery **and** scraps, treated as one body of work), the Watchlist (submissions from everyone you follow), and keyword searches.
- Deduplicates across runs using a SQLite store keyed on `(source, id)`, so re-runs only fetch what's new.
- Deduplicates by content using a SQLite store of SHA-256 file hashes: a download whose bytes are identical to a file already in the gallery (a cross-post or re-upload, even under a different id or source) is discarded instead of saved. Matching is exact-hash only, so distinct alternate versions ("alts") are never merged.
- Applies a shared blacklist (tags / keywords) across every module.
- Registers a Windows scheduled task on save, so runs happen on a configurable interval in the background.
- Includes a local HTTP gallery (launched from the config GUI) that lists both sources together in an e621-styled grid with pagination, a lightbox, and per-source filtering.

## Two integration patterns, side by side

The project is deliberately built around two contrasting integrations, which is most of the reason it exists as something worth looking at.

**e621 - sanctioned API client.** e621 publishes a documented JSON API with an explicit rate limit (≤1 req/sec), a User-Agent policy (descriptive UA with contact info), and HTTP Basic auth by username + API key. The `e621_mod.py` module is a straightforward HTTP client that respects all of this: it sleeps `1.1s` between requests, sends `FurScraper/1.0 (by <username> on e621)` as its User-Agent, and handles pagination plus newest-first short-circuiting against the seen store.

**FurAffinity - no API, so direct HTML scraping.** FA does not expose an official API, and its markup is the interface. `modules/fa_site.py` fetches FA pages directly and parses them, authenticating with the user's own session cookies (`a` and `b`). This is the only practical route to things like the Watchlist, but it sits in FA's terms-of-service gray area - see [Considerations](#considerations).

That module is a port of the parts of [faexport](https://github.com/Deer-Spangle/faexport) this project actually uses: gallery and scraps listings, keyword search, the new-submissions feed, and submission detail pages. Journals, comments, notes, shouts, watcher lists and favourites were left behind. FurScraper originally called a hosted faexport instance over HTTP; when the public one went offline, the choice was self-hosting a Ruby service in Docker or owning ~400 lines of parsing. The parsing won, and the whole Docker dependency went with it.

**The selectors are the fragile part.** They mirror upstream faexport, so when FA changes its HTML, diffing against that project is the fastest way to find what moved.

Everything downstream of the fetch is shared: dedup, blacklist filtering, download, gallery presentation. The common base lives in `modules/base.py`, the FA scraping in `modules/fa_site.py`, and the FA download/blacklist glue in `modules/fa_common.py`.

## Install

**[Download FurScraper.exe from the latest release](https://github.com/Tsidia/FurScraper/releases/latest)** and double-click it. Nothing else to install: Python, tkinter and every dependency are inside the executable.

Windows SmartScreen will warn you the first time, because the build is not code-signed. "More info" then "Run anyway".

The only requirement is Windows 10 / 11 (it uses `schtasks` for scheduling and `os.startfile` for shell integration). No runtime, no service, no container.

Then enable the modules you want, fill in credentials, and press **Save & Schedule** followed by **Run Now** for the first run.

### Running from source instead

```
python install.py
```

Installs dependencies into the same interpreter it writes into the Desktop shortcut, checks for `tkinter`, and creates the shortcut. Needs Python 3.9+ with `tkinter` from [python.org](https://www.python.org/downloads/), with the "tcl/tk and IDLE" option ticked; the Microsoft Store build often omits it. To install dependencies yourself: `pip install -r requirements.txt` (`requests` and `beautifulsoup4`).

### Building the executable

`.github/workflows/release.yml` builds it on a Windows runner and attaches it to a GitHub Release whenever a `v*` tag is pushed:

```
git tag v1.0.0 && git push origin v1.0.0
```

To build locally: `pip install pyinstaller && pyinstaller --clean --noconfirm furscraper.spec`, which produces `dist/FurScraper.exe`. The packaged app re-invokes itself as `FurScraper.exe --run` for scheduled runs, since a frozen build has no interpreter or `.py` files to point at.

## Using the FurAffinity modules

Two things are required, and both are on the **FA Auth** tab:

1. **Your FA account must use the Classic theme.** Modern's markup is entirely different and cannot be parsed. On FA: Settings → Site Preferences → Classic. If you forget, FurScraper says so explicitly rather than failing with a parse error.
2. **Session cookies `a` and `b`**, copied from a logged-in browser session. Every FA module needs them, not just the Watchlist, because every request to FA carries your session.

Requests are rate limited to roughly one per second, and first runs are capped at one page per artist, per folder, per query, so a fresh install does not stampede the site.

## Configuration

The GUI has one tab per module plus a shared blacklist and schedule/output settings. Config is saved to `%APPDATA%\FurScraper\config.json`.

| Tab | Contents |
| --- | --- |
| e621 | Username + API key (generate one at e621.net → Account → Manage API Access), then one tag search per line in e621 tag syntax. |
| FA Artists | One FurAffinity username per line. Both the gallery **and** the scraps of each user are walked newest-first until a known submission is hit. |
| FA Watchlist | Pulls new submissions from every user you follow. Authenticated-only. |
| FA Search | FA keyword queries, one per line. Date-ordered, newest first. Noisier than e621 tag search. |
| FA Auth | Classic-theme reminder, plus session cookies `a` and `b` copied from your logged-in FA browser session. Required by every FA module. |
| Blacklist | Tags / keywords, case-insensitive, applied across every module. |
| Schedule & Output | Run interval (minutes) and output folder. **Save & Schedule** (re-)registers the Windows task. |

## Gallery

**Open Gallery** in the config GUI starts a tiny HTTP server on `127.0.0.1` on a random free port (daemon thread inside the GUI process, so it dies when the GUI closes) and opens your browser to a dark, e621-styled grid of everything in the output folder. Tabs filter by source; the lightbox supports keyboard navigation (←/→/Esc) and deep-links each post back to its original page on e621 or FA. Videos stream with HTTP Range so seeking works.

The server is loopback-only, nothing goes external.

## Data layout

- Files: `<output_dir>\<id>.<ext>` for e621, `<output_dir>\fa_<id>.<ext>` for FurAffinity.
- Dedup DB: `%APPDATA%\FurScraper\seen.db` (SQLite; tables `seen(source, post_id)`, `search_state(source, query)`, and `file_hash(sha256, ...)` for content dedup).
- Log: `%APPDATA%\FurScraper\scraper.log`.
- Config: `%APPDATA%\FurScraper\config.json`.

## Considerations

Scraping sits in a space where what's technically possible, what a site permits, and what the people whose work is being collected would want aren't always the same thing. This project doesn't pretend otherwise.

**e621.** This module is a well-behaved API client. Rate limits are honored, the User-Agent is descriptive with contact info, and auth goes through the user's own API key. e621's API terms permit programmatic access for personal use and prohibit uses this tool does not perform (large-scale redistribution, AI training datasets). Nothing here runs against what the site asks of its clients.

**FurAffinity.** FA's ToS restricts automated access, and driving an authenticated client with your own session cookies is, read strictly, a violation. Enforcement in practice is rare and, where it happens, takes the form of account bans rather than anything escalated. Scraping FA at all is only necessary because FA publishes no API, which is the same reason the community built faexport. Going direct rather than through a hosted service means the traffic is yours, from your address, under your account - fewer moving parts, and no third party in the middle of your session. The modules self-rate-limit to ~1 req/sec and cap first-run pagination to a single page, but that's courtesy - not compliance. Running the FA side means making a conscious choice to use a gray-area integration.

**Artists.** The content this tool collects is public material posted on platforms built to display it, but mass archival changes the posture. Local copies bypass the engagement signals artists care about (views, favs, comments) and persist past anything the artist might later take down. FurScraper does not redistribute, republish, or feed content into any training pipeline - files live on the user's disk and that's it. That's the scope I'm comfortable shipping; anyone running it is making their own judgment on the same question.
