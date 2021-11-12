import os
import json

import torrentbd

# QBT Guide - https://github.com/qbittorrent/search-plugins/wiki/How-to-write-a-search-plugin


def test_load_settings_validation():
    test_settings = [
        ({}, {}),
        (
            {"username": "dummy", "password": "1234"},
            {
                "username": "dummy",
                "password": "1234",
                "freeleech": False,
                "internal": False,
                "active_only": False,
            },
        ),
        (
            {"username": "dummy", "password": "1234", "freeleech": True},
            {
                "username": "dummy",
                "password": "1234",
                "freeleech": True,
                "internal": False,
                "active_only": False,
            },
        ),
    ]

    for pair in test_settings:
        expected = pair[1]
        actual = torrentbd.load_settings(json.dumps(pair[0]))
        assert actual == expected


def test_search_result_parser():
    data = ""
    with open(
        os.path.join(
            os.path.abspath(os.path.dirname(__file__)),
            "data",
            "sample_search_result.html",
        )
    ) as file:
        data = file.read()

    parser = torrentbd.SearchResultParser()
    parser.feed(data)

    assert parser.cur_stage == torrentbd.TABLE_ENDED
    assert parser.cur_row == {}
    assert len(parser.result_set) == 15


def test_join_url_paths():
    data = [
        (("http://alpha.com", "/down"), "http://alpha.com/down"),
        (("http://alpha.com/", "down"), "http://alpha.com/down"),
        (("http://alpha.com/", "/down"), "http://alpha.com/down"),
    ]

    for pair in data:
        expected = pair[1]
        actual = torrentbd.join_url_paths(*pair[0])
        assert actual == expected
