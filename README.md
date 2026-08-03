# FurScraper

A personal archiver for two furry art sites - [e621](https://e621.net) and [FurAffinity](https://www.furaffinity.net) with a Windows GUI for configuration, a scheduled background runner, and a local web gallery that presents posts from both sources in a single browsable view.
Available for download at: **[FurScraper.exe](https://github.com/Tsidia/FurScraper/releases/latest/download/FurScraper.exe)**

## What it does

- Pulls new posts from e621 tag searches.
- Pulls new submissions from FurAffinity: specific artists, watchlist, keyword searches.
- Applies universal blacklist.
- Stores all data in a local gallery.
- Works silently in the background via windows scheduled tasks.

## Install

All you have to do is run the exe you downloaded. If you later decide to move it somewhere else you might have to run it again. When you run FurScraper for the first time you may receive a warning about running unknown software. I'm not rich enough to have it certified :(

### Running from source (if you're a dev)

```
pip install -r requirements.txt
python install.py
pip install pyinstaller && pyinstaller --clean --noconfirm furscraper.spec
```
Which produces `dist/FurScraper.exe`. The packaged app re-invokes itself as `FurScraper.exe --run` for scheduled runs, since a frozen build has no interpreter or `.py` files.

Config is saved to `%APPDATA%\FurScraper\config.json`. **Save & schedule** writes it and (re-)registers the Windows task.

Gallery lives in your browser under a stable link you can bookmark. All data remains on your local device. 

## Data layout

- Files: `<output_dir>\<id>.<ext>` for e621, `<output_dir>\fa_<id>.<ext>` for FurAffinity.
- Dedup DB: `%APPDATA%\FurScraper\seen.db` (SQLite; tables `seen(source, post_id)`, `search_state(source, query)`, and `file_hash(sha256, ...)` for content dedup).
- Log: `%APPDATA%\FurScraper\scraper.log`.
- Config: `%APPDATA%\FurScraper\config.json`.

## Two scraping philosophies

**e621 - sanctioned API client.** e621 publishes a JSON API, a User-Agent policy, and HTTP auth.

**FurAffinity - HTML scraping.** FA does not expose an official API, the markup is the interface. `modules/fa_site.py` fetches FA pages directly and parses them, authenticating with the user's session cookies. It sits in FA's terms-of-service gray area - see [Considerations](#considerations).

FA module uses parts of [faexport](https://github.com/Deer-Spangle/faexport): gallery and scraps listings, keyword search, the new-submissions feed, and submission detail pages.

**The selectors are fragile.** When FA changes its HTML, everything breaks.

## Considerations

Scraping sits in a space where what's technically possible, what a site permits, and what the people whose work is being collected would want aren't always the same thing.

**e621.** This module is a well-behaved API client. Rate limits are honored, the User-Agent is descriptive with contact info, and auth goes through the user's API key. e621's API terms permit programmatic access for personal use.

**FurAffinity.** FA's ToS restricts automated access. Driving an authenticated client with session cookies is a violation. Enforcement in practice is rare. The modules rate-limit to 1 reqest per second and cap first-run pagination to a single page out of courtesy.

**Artists.** The content this tool collects is public material posted on platforms built to display it. Local copies bypass the engagement signals artists care about: views, favs, comments. They also ignore takedown requests. FurScraper does not redistribute, republish, or feed content anywhere.
