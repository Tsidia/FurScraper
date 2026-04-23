# FurScraper

A personal archiver for two furry art sites - [e621](https://e621.net) and [FurAffinity](https://www.furaffinity.net) with a Windows GUI for configuration, a scheduled background runner, and a local web gallery that presents posts from both sources in a single browsable view.

## What it does

- Pulls new posts from configurable e621 tag searches.
- Pulls new submissions from three FurAffinity channels: specific artists' galleries, the Watchlist (submissions from everyone you follow), and keyword searches.
- Deduplicates across runs using a SQLite store keyed on `(source, id)`, so re-runs only fetch what's new.
- Applies a shared blacklist (tags / keywords) across every module.
- Registers a Windows scheduled task on save, so runs happen on a configurable interval in the background.
- Includes a local HTTP gallery (launched from the config GUI) that lists both sources together in an e621-styled grid with pagination, a lightbox, and per-source filtering.

## Two integration patterns, side by side

The project is deliberately built around two contrasting integrations, which is most of the reason it exists as something worth looking at.

**e621 - sanctioned API client.** e621 publishes a documented JSON API with an explicit rate limit (≤1 req/sec), a User-Agent policy (descriptive UA with contact info), and HTTP Basic auth by username + API key. The `e621_mod.py` module is a straightforward HTTP client that respects all of this: it sleeps `1.1s` between requests, sends `FurScraper/1.0 (by <username> on e621)` as its User-Agent, and handles pagination plus newest-first short-circuiting against the seen store.

**FurAffinity - no API, so a community wrapper.** FA does not expose an official API. The FA modules talk to [faexport](https://faexport.spangle.org.uk), a third-party service that scrapes FA's HTML pages and re-exposes them as JSON. Authentication is by forwarding the user's own FA session cookies (`a` and `b`) in a request header, which faexport replays against FA. This works and is the only practical route to endpoints like the Watchlist, but it sits in FA's terms-of-service gray area - see [Considerations](#considerations).

Everything downstream of the fetch is shared: dedup, blacklist filtering, download, gallery presentation. The common base lives in `modules/base.py`, and the FA HTTP client in `modules/fa_common.py`.

## Requirements

- Windows 10 / 11 (uses `schtasks` for scheduling and `os.startfile` for shell integration).
- Python 3.9+ with `tkinter` (bundled with standard Windows installers).
- `requests`.

## Setup

```
pip install requests
python install.py        # drops a "FurScraper" shortcut on your Desktop
```

## Configuration

The GUI has one tab per module plus a shared blacklist and schedule/output settings. Config is saved to `%APPDATA%\FurScraper\config.json`.

| Tab | Contents |
| --- | --- |
| e621 | Username + API key (generate one at e621.net → Account → Manage API Access), then one tag search per line in e621 tag syntax. |
| FA Artists | One FurAffinity username per line. Each user's gallery is walked newest-first until a known submission is hit. |
| FA Watchlist | Pulls new submissions from every user you follow. Authenticated-only. |
| FA Search | FA keyword queries, one per line. Date-ordered, newest first. Noisier than e621 tag search. |
| FA Auth | Session cookies `a` and `b`, copied from your logged-in FA browser session. Required for the Watchlist and mature content. |
| Blacklist | Tags / keywords, case-insensitive, applied across every module. |
| Schedule & Output | Run interval (minutes) and output folder. **Save & Schedule** (re-)registers the Windows task. |

## Gallery

**Open Gallery** in the config GUI starts a tiny HTTP server on `127.0.0.1` on a random free port (daemon thread inside the GUI process, so it dies when the GUI closes) and opens your browser to a dark, e621-styled grid of everything in the output folder. Tabs filter by source; the lightbox supports keyboard navigation (←/→/Esc) and deep-links each post back to its original page on e621 or FA. Videos stream with HTTP Range so seeking works.

The server is loopback-only, nothing goes external.

## Data layout

- Files: `<output_dir>\<id>.<ext>` for e621, `<output_dir>\fa_<id>.<ext>` for FurAffinity.
- Dedup DB: `%APPDATA%\FurScraper\seen.db` (SQLite; tables `seen(source, post_id)` and `search_state(source, query)`).
- Log: `%APPDATA%\FurScraper\scraper.log`.
- Config: `%APPDATA%\FurScraper\config.json`.

## Considerations

Scraping sits in a space where what's technically possible, what a site permits, and what the people whose work is being collected would want aren't always the same thing. This project doesn't pretend otherwise.

**e621.** This module is a well-behaved API client. Rate limits are honored, the User-Agent is descriptive with contact info, and auth goes through the user's own API key. e621's API terms permit programmatic access for personal use and prohibit uses this tool does not perform (large-scale redistribution, AI training datasets). Nothing here runs against what the site asks of its clients.

**FurAffinity.** FA's ToS restricts automated access, and driving an authenticated client with forwarded session cookies is, read strictly, a violation. Enforcement in practice is rare and, where it happens, takes the form of account bans rather than anything escalated. faexport exists as a community project precisely because FA publishes no API. The modules self-rate-limit to ~1 req/sec and cap first-run pagination to a single page, but that's courtesy - not compliance. Running the FA side means making a conscious choice to use a gray-area integration.

**Artists.** The content this tool collects is public material posted on platforms built to display it, but mass archival changes the posture. Local copies bypass the engagement signals artists care about (views, favs, comments) and persist past anything the artist might later take down. FurScraper does not redistribute, republish, or feed content into any training pipeline - files live on the user's disk and that's it. That's the scope I'm comfortable shipping; anyone running it is making their own judgment on the same question.
