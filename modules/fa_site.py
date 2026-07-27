"""Direct FurAffinity client.

FA publishes no API, so this scrapes its HTML directly. The parsing is a port of
the parts of faexport (https://github.com/Deer-Spangle/faexport) that FurScraper
actually uses: gallery/scraps listings, keyword search, the new-submissions
notification feed, and submission detail pages. Journals, comments, notes,
shouts, watcher lists and favourites are deliberately not ported.

Two things to know if FA changes and this breaks:

  * FA must be set to the Classic theme. Modern's markup is completely different
    and nothing here can parse it. That is detected explicitly and reported as
    FAStyleError rather than being allowed to fail as a confusing parse error.
  * Selectors below mirror upstream faexport. When FA changes its HTML, diffing
    against that project is the fastest way to find what moved.
"""
import re
import time
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

FA_BASE = "https://www.furaffinity.net"
USER_AGENT = "FurScraper/1.0"
REQ_DELAY = 1.1        # FA is not an API; stay well under a request per second
MAX_RETRIES = 5
PARSER = "html.parser"  # no lxml, so the packaged exe stays dependency-light

# Search parameters, mirroring faexport's SEARCH_DEFAULTS. Underscores become
# dashes in FA's form, and multi-valued fields become "<name>-<value>=on".
SEARCH_DEFAULTS = {
    "perpage": "72",
    "order-by": "date",
    "order-direction": "desc",
    "range": "all",
    "mode": "extended",
}
SEARCH_RATINGS = ("general", "mature", "adult")
SEARCH_TYPES = ("art", "flash", "photo", "music", "story", "poetry")


# ---------- errors ----------

class FAError(RuntimeError):
    """Base for everything raised here. Messages are user-facing."""


class FALoginError(FAError):
    def __init__(self, url):
        super().__init__(
            f"FurAffinity did not accept the session cookies ({url}).\n\n"
            "Re-copy cookies 'a' and 'b' from a logged-in FA browser session on "
            "the FA Auth tab. They break when you log out, clear cookies, or "
            "change your password."
        )


class FAStyleError(FAError):
    def __init__(self, url):
        super().__init__(
            f"Your FurAffinity account is not using the Classic theme ({url}).\n\n"
            "FurScraper can only read Classic. On FA go to Settings, then Site "
            "Preferences, and set the site theme to Classic, then try again."
        )


class FAGuestAccessError(FAError):
    def __init__(self, url):
        super().__init__(
            f"That page is restricted to registered users and the request was "
            f"not logged in ({url}). Check the cookies on the FA Auth tab."
        )


class FANotFoundError(FAError):
    def __init__(self, url):
        super().__init__(f"Not found on FA (deleted or pending deletion): {url}")


class FANoUserError(FAError):
    def __init__(self, url):
        super().__init__(f"No such FurAffinity user: {url}")


class FAAccountDisabledError(FAError):
    def __init__(self, url):
        super().__init__(f"That FA account is disabled: {url}")


class FAContentFilterError(FAError):
    def __init__(self, url):
        super().__init__(
            f"FA's content filter hid this submission ({url}). Your FA account's "
            "maturity settings need to allow it."
        )


class FACloudflareError(FAError):
    def __init__(self, url):
        super().__init__(
            f"Cloudflare is blocking access to FurAffinity ({url}).\n\n"
            "Nothing to configure; this clears when FA's protection settles. "
            "Try again later."
        )


class FASlowdownError(FAError):
    def __init__(self, url):
        super().__init__(
            f"FurAffinity is rate limiting us ({url}). Try again in a few minutes."
        )


class FASystemError(FAError):
    def __init__(self, url):
        super().__init__(f"FurAffinity returned a system error: {url}")


class FAStatusError(FAError):
    def __init__(self, url, status):
        super().__init__(f"Unexpected HTTP {status} from {url}")


class FAFormError(FAError):
    def __init__(self, url):
        super().__init__(f"FA rejected the search form: {url}")


# ---------- helpers ----------

def _text(node):
    return node.get_text() if node is not None else ""


def _last_path(path):
    return (path or "").rstrip("/").split("/")[-1]


def _abs(url):
    """FA emits protocol-relative URLs like //d.furaffinity.net/..."""
    if not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return FA_BASE + url
    return url


def _field(lines, name):
    """Pull "Field: value" out of the info block, in either of FA's two shapes."""
    pattern = re.compile(rf"^{re.escape(name)}: (.+)$")
    for line in lines:
        m = pattern.match(line)
        if m:
            return m.group(1)
    # The other shape puts the label and value on consecutive lines.
    for i, line in enumerate(lines):
        if line == f"{name}:" and i + 1 < len(lines):
            return lines[i + 1]
    return None


def _pick_date(tag):
    if tag is None:
        return None
    content = tag.get_text()
    return tag.get("title") if "ago" in content else content


class FASite:
    """Scrapes FurAffinity directly. One instance per run."""

    def __init__(self, cookie_a="", cookie_b="", delay=REQ_DELAY, logger=None):
        self.delay = delay
        self.logger = logger
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        if cookie_a and cookie_b:
            # Must go in the cookie jar, not a raw header: requests drops the
            # Cookie header when following a redirect and rebuilds it from the
            # jar. FA redirects plenty (/msg/submissions/new -> /msg/submissions/),
            # so a header-only session silently arrives logged out.
            for name, value in (("a", cookie_a), ("b", cookie_b)):
                self.session.cookies.set(name, value, domain=".furaffinity.net")

    # ---------- fetching ----------

    def _url(self, path):
        return f"{FA_BASE}/{path.lstrip('/')}"

    def _fetch(self, path):
        """GET a page, translate FA's error pages into exceptions, return soup."""
        url = self._url(path)
        attempt = 0
        sleep_for = 0.0
        while True:
            time.sleep(self.delay)
            try:
                r = self.session.get(url, timeout=60)
            except requests.ConnectionError:
                attempt += 1
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(sleep_for)
                sleep_for += 0.5
                continue

            if r.status_code == 200:
                soup = BeautifulSoup(r.text, PARSER)
                self._check_errors(soup, url)
                return soup

            # Transient upstream failures are worth retrying.
            if r.status_code in (502, 520):
                attempt += 1
                if attempt >= MAX_RETRIES:
                    raise FAStatusError(url, r.status_code)
                time.sleep(sleep_for)
                sleep_for += 0.5
                continue

            self._handle_error_status(r, url)
            raise FAStatusError(url, r.status_code)

    def _handle_error_status(self, r, url):
        """Recognise the failures FA signals with a non-200 plus a specific page."""
        if r.status_code not in (400, 403, 503):
            return
        soup = BeautifulSoup(r.text, PARSER)

        if r.status_code == 403 and soup.select_one("#challenge-error-title"):
            raise FACloudflareError(url)

        title = _text(soup.find("title"))
        if (
            r.status_code == 503
            and "Error 503 --" in title
            and "you are requesting web pages too fast and are being rate limited" in r.text
        ):
            raise FASlowdownError(url)

        if r.status_code == 400 and title == "System Error":
            msg = _text(soup.select_one("table.maintable td.alt1 font"))
            if "This user cannot be found" in msg or "User not found!" in msg:
                raise FANoUserError(url)

    def _check_errors(self, soup, url):
        """Port of faexport's check_errors: FA returns 200 for most failures."""
        head = soup.find("title")
        if head is None:
            raise FAError(f"FA returned a page with no title: {url}")

        # The theme check doubles as the login check, because a logged-out
        # session is served Modern regardless of account settings.
        stylesheet = soup.select_one("head link[rel='stylesheet']")
        href = stylesheet.get("href", "") if stylesheet else ""
        if not href.startswith("/themes/classic/"):
            notice = soup.select_one("#site-content section.notice-message")
            if notice is not None:
                header = _text(notice.find("h2"))
                content = _text(notice.select_one(".redirect-message"))
                if header == "System Message" and (
                    "has elected to make it available to registered users only." in content
                ):
                    raise FAGuestAccessError(url)

            nav = soup.select_one("nav#ddmenu span.top-heading a")
            if nav is not None and nav.get("href") == "/login":
                raise FALoginError(url)

            raise FAStyleError(url)

        if _text(head) == "System Error":
            msg = _text(soup.select_one("table.maintable td.alt1 font"))
            if "you are trying to find is not in our database." in msg:
                raise FANotFoundError(url)
            if "This user cannot be found" in msg or "User not found!" in msg:
                raise FANoUserError(url)
            raise FASystemError(url)

        # "System Message" pages: a 200 with the real problem in the body.
        for table in soup.select("table.maintable"):
            if table.get("id") == "admin_notice_do_not_adblock":
                continue
            cat = table.select_one("td.cat")
            if cat is None or _text(cat).strip() != "System Message":
                continue
            body = _text(table.select_one("td.alt1"))
            if (
                "has voluntarily disabled access to their account and all of its contents." in body
                or "Access has been disabled to the account and contents of user" in body
            ):
                raise FAAccountDisabledError(url)
            if (
                "Provided username not found in the database." in body
                or re.search(r'The username "[^"]+" could not be found\.', body)
                or re.search(r'User "[^"]+" was not found in our database\.', body)
            ):
                raise FANoUserError(url)
            if "You are not allowed to view this image due to the content filter settings." in body:
                raise FAContentFilterError(url)
            if "The page you are trying to reach is currently pending deletion" in body:
                raise FANotFoundError(url)
            break

    # ---------- listings ----------

    @staticmethod
    def _tile_ids(soup):
        """Submission IDs from a gallery/search grid, newest first."""
        ids = []
        for fig in soup.select(".gallery > figure"):
            raw = fig.get("id") or ""
            sid = re.sub(r"sid[-_]", "", raw)
            if sid:
                ids.append(sid)
        return ids

    def submissions(self, user, folder="gallery", page=1):
        """One page of a user's gallery or scraps. folder: 'gallery' or 'scraps'."""
        if folder not in ("gallery", "scraps"):
            raise ValueError(f"unsupported folder: {folder}")
        soup = self._fetch(f"{folder}/{quote(user)}/{int(page)}/")
        return self._tile_ids(soup)

    def search(self, query, page=1):
        params = {"page": max(1, int(page)), "q": query}
        params.update(SEARCH_DEFAULTS)
        for rating in SEARCH_RATINGS:
            params[f"rating-{rating}"] = "on"
        for kind in SEARCH_TYPES:
            params[f"type-{kind}"] = "on"
        soup = self._fetch("search/?" + urlencode(params))
        # Present even for a search with no matches; absent if the form failed.
        if soup.select_one("#search-results") is None:
            raise FAFormError(self._url("search/"))
        return self._tile_ids(soup)

    def new_submissions(self, from_id=None):
        """The watchlist feed: submissions from everyone you follow."""
        path = "msg/submissions/new"
        if from_id:
            path += f"~{from_id}@72/"
        soup = self._fetch(path)
        items = []
        for fig in soup.select(".gallery > figure"):
            links = fig.find_all("a")
            if len(links) < 2:
                continue
            title_link = links[1]
            uploader = links[2] if len(links) > 2 else None
            items.append(
                {
                    "id": _last_path(title_link.get("href")),
                    "title": title_link.get_text(),
                    "link": _abs(title_link.get("href")),
                    "name": uploader.get_text() if uploader else "",
                    "profile_name": _last_path(uploader.get("href")) if uploader else "",
                }
            )
        return {"new_submissions": items}

    # ---------- submission detail ----------

    def submission(self, sid):
        """Detail for one submission. 'download' and 'keywords' are what the
        downloader needs; the rest is best-effort and may be None."""
        soup = self._fetch(f"view/{sid}/")
        return self._parse_submission(sid, soup)

    def _parse_submission(self, sid, soup):
        url = self._url(f"view/{sid}/")
        tables = soup.select("div#page-submission table.maintable table.maintable")
        if not tables:
            raise FAError(
                f"Could not parse submission page {url}. FA's layout may have "
                "changed, or the account is not on the Classic theme."
            )
        block = tables[-1]
        raw_info = block.select_one("td.alt1")
        info_lines = []
        if raw_info is not None:
            info_lines = [
                line.strip() for line in raw_info.get_text().splitlines() if line.strip()
            ]

        # The download link is the one thing we cannot proceed without.
        download = None
        for a in soup.select("#page-submission td.alt1 div.actions a"):
            if a.get_text().strip() == "Download":
                download = _abs(a.get("href"))
                break
        if not download:
            raise FAError(
                f"No download link on {url}. The submission may be deleted, or "
                "hidden by your FA content-filter settings."
            )

        keywords = []
        if raw_info is not None:
            keywords = [
                k.get_text().strip()
                for k in raw_info.select("div#keywords a")
                if k.get_text().strip()
            ]

        title_block = block.select_one(".classic-submission-title")
        profile_link = soup.select_one(".classic-submission-title.information a")
        img = soup.select_one("img#submissionImg")
        rating_img = raw_info.select_one("div img") if raw_info is not None else None

        return {
            "id": str(sid),
            "title": _text(title_block.find("h2")) if title_block else "",
            "name": _text(profile_link),
            "profile_name": _last_path(profile_link.get("href")) if profile_link else "",
            "link": url,
            "posted": _pick_date(raw_info.select_one(".popup_date") if raw_info else None),
            "download": download,
            "full": _abs(img.get("data-fullview-src")) if img else None,
            "category": _field(info_lines, "Category"),
            "species": _field(info_lines, "Species"),
            "gender": _field(info_lines, "Gender"),
            "rating": (rating_img.get("alt") or "").replace(" rating", "")
            if rating_img
            else None,
            "keywords": keywords,
        }
