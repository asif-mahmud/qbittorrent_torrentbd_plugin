# VERSION: 1.0

import os
import io
import gzip
import json
import logging
import tempfile
import urllib.request as request
import urllib.parse as parse
import html.parser as htmlparser
import http.cookiejar as httpcookiejar
import novaprinter

# Global variables
SETTINGS_SEARCH_PATHS = [
    os.path.expanduser("~"),  # user's home directory
    os.path.abspath(os.path.dirname(__file__)),  # script directory
    os.path.abspath(os.getcwd()),  # current working directory
]
SETTINGS_FILE_NAMES = [
    "torrentbdrc.json",
    ".torrentbdrc.json",
]
SETTINGS_SCHEMA = {
    "username": {
        "type": str,
        "required": True,
        "default": "",
    },
    "password": {
        "type": str,
        "required": True,
        "default": "",
    },
    "freeleech": {
        "type": bool,
        "required": False,
        "default": False,
    },
    "internal": {
        "type": bool,
        "required": False,
        "default": False,
    },
    "active_only": {
        "type": bool,
        "required": False,
        "default": False,
    },
    "enable_logging": {
        "type": bool,
        "required": False,
        "default": False,
    },
}
TBD_ENGINE_URL = "https://www.torrentbd.com"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/95.0.4638.54 Safari/537.36"


def read_settings_file():
    """Reads settings file from specific directories."""
    found_path = ""

    # look in the predefined paths
    for path in SETTINGS_SEARCH_PATHS:
        for settings_filename in SETTINGS_FILE_NAMES:
            full_path = os.path.join(path, settings_filename)
            if os.path.exists(full_path):
                found_path = full_path
                break
        if found_path:
            break

    # if settings is not found, then return
    if not found_path:
        return ""

    # read settings
    data = ""
    try:
        with open(found_path, "r") as file:
            data = file.read()
    except Exception:
        return ""
    return data


def load_settings(settings_str=""):
    """Loads and verifies TorrentBD settings."""
    # read from disk if not provided in argument
    if not settings_str:
        settings_str = read_settings_file()

    # parse and validate
    if not settings_str:
        return {}

    # parse and validate
    parsed = {}
    try:
        parsed = json.loads(settings_str)
        for key in SETTINGS_SCHEMA:
            # check type
            if key in parsed:
                assert isinstance(parsed[key], SETTINGS_SCHEMA[key]["type"])
            # check required
            if SETTINGS_SCHEMA[key]["required"]:
                assert key in parsed
            # setup defaults
            if key not in parsed and not SETTINGS_SCHEMA[key]["required"]:
                parsed[key] = SETTINGS_SCHEMA[key]["default"]
    except Exception:
        return {}
    return parsed


# HTML parser tag stages
NOT_STARTED = 1  # not started yet
TABLE_STARTED = 2  # <table>
TABLE_ENDED = 3  # </table>
ROW_STARTED = 4  # <tr>
ROW_ENDED = 5  # </tr>
COL_1_STARTED = 6  # 1st <td>
COL_1_ENDED = 7  # 1st </td>
COL_2_STARTED = 8  # 2nd <td>
COL_2_ENDED = 9  # 2nd </td>
COL_3_STARTED = 10  # 3rd <td>
COL_3_ENDED = 11  # 3rd </td>
TORRENT_DESC_LINK_TAG = (
    12  # <a class="ttorr-title" href="{desc_link}">{name}</a> [2nd td > div > a]
)
FILE_SIZE_TAG = 13  # <div title="File Size">{size}</div> [2nd td > div]
SEEDERS_TAG = 14  # <div title="Seeders online">{seeds}</div> [2nd td > div > div]
LEECHES_TAG = 15  # <div title="Leechers">{leech}</div> [2nd td > div > div]
TORRENT_LINK_TAG = 16  # <a href="{link}"></a> [3rd td > a]

# dictionary keys based on qBittorrent spec
QBT_KEY_LINK = "link"
QBT_KEY_NAME = "name"
QBT_KEY_SIZE = "size"
QBT_KEY_SEEDS = "seeds"
QBT_KEY_LEECH = "leech"
QBT_KEY_DESC_LINK = "desc_link"
QBT_KEY_ENGINE_URL = "engine_url"

# torrentbd response tags/attributes
TBD_DESC_LINK_CLASS = "ttorr-title"
TBD_FILE_SIZE_TITLE = "File Size"
TBD_SEEDERS_TITLE = "Seeders online"
TBD_LEECHERS_TITLE = "Leechers"


class SearchResultParser(htmlparser.HTMLParser):
    """HTML parser for retrieved search result."""

    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.result_set = []
        self.cur_stage = NOT_STARTED
        self.cur_row = {}

    def get_title_attribute(self, attrs):
        """Get title attributes from attributes list"""
        title = ""
        for attr in attrs:
            if attr[0] == "title":
                title = attr[1]
                break
        return title

    def handle_starttag(self, tag, attrs):
        # print("Tag start:", tag, attrs)
        if tag == "table":
            self.cur_stage = TABLE_STARTED
        elif tag == "tr":
            self.cur_stage = ROW_STARTED
            self.cur_row = {}
        elif tag == "td":
            if self.cur_stage == ROW_STARTED:
                self.cur_stage = COL_1_STARTED
            elif self.cur_stage == COL_1_ENDED:
                self.cur_stage = COL_2_STARTED
            elif self.cur_stage == COL_2_ENDED:
                self.cur_stage = COL_3_STARTED
        elif tag == "a":
            if self.cur_stage == COL_2_STARTED:
                # collect the href, we'll only use it when
                # cur_stage is set to TORRENT_DESC_LINK_TAG
                desc_link = ""
                for attr in attrs:
                    if attr[0] == "class" and attr[1] == TBD_DESC_LINK_CLASS:
                        self.cur_stage = TORRENT_DESC_LINK_TAG
                    elif attr[0] == "href":
                        desc_link = attr[1]
                if self.cur_stage == TORRENT_DESC_LINK_TAG:
                    self.cur_row[QBT_KEY_DESC_LINK] = parse.urljoin(
                        TBD_ENGINE_URL,
                        desc_link,
                    )
            elif self.cur_stage == COL_3_STARTED:
                self.cur_stage = TORRENT_LINK_TAG
                for attr in attrs:
                    if attr[0] == "href":
                        self.cur_row[QBT_KEY_LINK] = parse.urljoin(
                            TBD_ENGINE_URL,
                            attr[1],
                        )
        elif tag == "div":
            title = self.get_title_attribute(attrs)
            if self.cur_stage == COL_2_STARTED:
                if title == TBD_FILE_SIZE_TITLE:
                    self.cur_stage = FILE_SIZE_TAG
                elif title == TBD_SEEDERS_TITLE:
                    self.cur_stage = SEEDERS_TAG
                elif title == TBD_LEECHERS_TITLE:
                    self.cur_stage = LEECHES_TAG

    def handle_endtag(self, tag):
        # print("Tag end:", tag)
        if tag == "table":
            self.cur_stage = TABLE_ENDED
        elif tag == "tr":
            self.cur_stage = ROW_ENDED
            self.cur_row[QBT_KEY_ENGINE_URL] = TBD_ENGINE_URL
            self.result_set.append(self.cur_row)
            self.cur_row = {}
        elif tag == "a":
            if self.cur_stage == TORRENT_DESC_LINK_TAG:
                self.cur_stage = COL_2_STARTED
            elif self.cur_stage == TORRENT_LINK_TAG:
                self.cur_stage = COL_3_STARTED
        elif tag == "div":
            if self.cur_stage in [FILE_SIZE_TAG, SEEDERS_TAG, LEECHES_TAG]:
                self.cur_stage = COL_2_STARTED
        elif tag == "td":
            if self.cur_stage == COL_1_STARTED:
                self.cur_stage = COL_1_ENDED
            elif self.cur_stage == COL_2_STARTED:
                self.cur_stage = COL_2_ENDED
            elif self.cur_stage == COL_3_STARTED:
                self.cur_stage = COL_3_ENDED

    def handle_data(self, data):
        # print("Data:", data)
        data = data.strip().replace(",", "")
        if self.cur_stage == TORRENT_DESC_LINK_TAG:
            self.cur_row[QBT_KEY_NAME] = data
        elif self.cur_stage == FILE_SIZE_TAG:
            self.cur_row[QBT_KEY_SIZE] = data
        elif self.cur_stage == SEEDERS_TAG:
            self.cur_row[QBT_KEY_SEEDS] = data
        elif self.cur_stage == LEECHES_TAG:
            self.cur_row[QBT_KEY_LEECH] = data


class Client(object):
    def __init__(self, host=TBD_ENGINE_URL):
        self.host = host
        self.cookiejar = httpcookiejar.CookieJar()
        self.opener = request.build_opener(
            request.HTTPCookieProcessor(self.cookiejar),
        )

    def post(self, path="", data=None, headers={}):
        url = parse.urljoin(self.host, path)
        form_data = parse.urlencode(data).encode("utf-8")
        req = request.Request(
            url,
            data=form_data,
            method="POST",
            headers=headers,
            origin_req_host=self.host,
        )
        req.add_header(
            "Content-Type",
            "application/x-www-form-urlencoded; charset=UTF-8",
        )
        req.add_header(
            "User-Agent",
            USER_AGENT,
        )
        return self.opener.open(req)

    def get(self, path="", params=None, headers={}):
        url = parse.urljoin(self.host, path)
        search_params = ""
        if params:
            search_params = parse.urlencode(params)
            url = url + "?" + search_params
        req = request.Request(
            url,
            headers=headers,
            origin_req_host=self.host,
        )
        req.add_header(
            "User-Agent",
            USER_AGENT,
        )
        return self.opener.open(req)


class Logger(object):
    def __init__(self, settings):
        self.settings = settings
        self.logger = logging.getLogger("TorrentBD")
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(
            os.path.expanduser("~/.torrentbd.log"),
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(handler)

    def log(self, msg, *args):
        if self.settings["enable_logging"]:
            self.logger.info(msg, *args)


class torrentbd(object):
    url = TBD_ENGINE_URL
    name = "TorrentBD"
    supported_categories = {
        "all": "",
        "movies": "Movies",
        "tv": "TV",
        "music": "Music",
        "games": "Games",
        "anime": "Anime",
        "software": ["65", "18", "19", "20"],
    }

    def __init__(self):
        self.client = Client(self.url)
        self.logged_in = False
        self.settings = load_settings()
        self.logger = Logger(self.settings)
        self.logger.log("initialized")
        self.logger.log("settings: {}".format(json.dumps(self.settings)))

    def login_request(self):
        self.logger.log("signing in")
        data = {
            "username": self.settings["username"],
            "password": self.settings["password"],
            "auth_login": "",
            "recaptcha_token": "",
            "otp": "",
            "login_phase": "1",
            "_remember": "yes",
            "extra": "",
        }
        res = self.client.post("/ajtakelogin.php", data)
        self.logger.log("sign-in res status: {}".format(res.status))
        if res.status != 200:
            return
        try:
            res_data = res.read().decode("utf-8")
            data = json.loads(res_data)
            self.logged_in = data["success"]
            self.logger.log("sign-in status: {}".format(self.logged_in))
        except Exception:
            return

    def download_torrent(self, url):
        """
        Providing this function is optional.
        It can however be interesting to provide your own torrent download
        implementation in case the search engine in question does not allow
        traditional downloads (for example, cookie-based download).
        """
        if self.settings and not self.logged_in:
            self.login_request()
        if not self.logged_in or not url:
            return
        self.logger.log("downloading torrent file, url: {}".format(url))
        res = self.client.get(url)
        self.logger.log("download torrent res status: {}".format(res.status))
        if res.status != 200:
            return
        data = res.read()
        tmp_fd, tmp_file_path = tempfile.mkstemp(prefix="tbd_")
        tmp_file = os.fdopen(tmp_fd, "wb")
        # the following part is shamelessly borrowed from
        # helpers.py file, found here -
        # https://github.com/qbittorrent/qBittorrent/blob/master/src/searchengine/nova3/helpers.py
        if data[:2] == b"\x1f\x8b":
            # Data is gzip encoded, decode it
            self.logger.log("gzipped file found. uncompressing.")
            compressedstream = io.BytesIO(data)
            gzipper = gzip.GzipFile(fileobj=compressedstream)
            extracted_data = gzipper.read()
            data = extracted_data

        tmp_file.write(data)
        tmp_file.close()
        self.logger.log("torrent file saved in {}".format(tmp_file_path))
        print("{} {}".format(tmp_file_path, url))

    def search(self, what, cat="all"):
        """
        Here you can do what you want to get the result from the search engine website.
        Everytime you parse a result line, store it in a dictionary
        and call the prettyPrint(your_dict) function.

        `what` is a string with the search tokens, already escaped (e.g. "Ubuntu+Linux")
        `cat` is the name of a search category in ('all', 'movies', 'tv',
        'music', 'games', 'anime', 'software', 'pictures', 'books')
        """
        if self.settings and not self.logged_in:
            self.login_request()
        search_term = what.strip()
        if not self.logged_in or not search_term:
            return
        # we have to unquote, cause qbittorrent sends us
        # quoted string, but here we quote data before sending
        # so unquoting before processing
        search_term = parse.unquote(search_term)
        catgories = ""
        if cat in self.supported_categories:
            catgories = self.supported_categories[cat]

        data = [
            ("page", 1),
            ("kuddus_searchtype", "torrents"),
            ("kuddus_searchkey", search_term),
            ("searchParams[sortBy]", "seeders"),
            ("searchParams[secondary_filters_extended]", ""),
        ]
        if isinstance(catgories, list):
            for category in catgories:
                data.append(("searchParams[torrentcats][]", category))
        elif catgories:
            data.append(("searchParams[torrentcats][]", catgories))

        if self.settings["freeleech"]:
            data.append(("searchParams[tertiary_filters][]", "freeleech"))
        if self.settings["internal"]:
            data.append(("searchParams[tertiary_filters][]", "internal"))
        if self.settings["active_only"]:
            data.append(("searchParams[tertiary_filters][]", "active"))

        self.logger.log("searching")
        self.logger.log("search params: {}".format(json.dumps(data)))
        self.logger.log("search term: {}".format(search_term))

        res = self.client.post("/ajsearch.php", data)
        self.logger.log("search res status: {}".format(res.status))
        if res.status != 200:
            return
        res_body = res.read().decode("utf-8")
        self.logger.log("search result len: {}".format(len(res_body)))
        parser = SearchResultParser()
        parser.feed(res_body)
        self.logger.log("found {} results".format(len(parser.result_set)))
        for result in parser.result_set:
            novaprinter.prettyPrinter(result)
