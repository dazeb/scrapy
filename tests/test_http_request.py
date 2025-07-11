import json
import re
import warnings
import xmlrpc.client
from typing import Any
from unittest import mock
from urllib.parse import parse_qs, unquote_to_bytes

import pytest

from scrapy.http import (
    FormRequest,
    Headers,
    HtmlResponse,
    JsonRequest,
    Request,
    XmlRpcRequest,
)
from scrapy.http.request import NO_CALLBACK
from scrapy.utils.httpobj import urlparse_cached
from scrapy.utils.python import to_bytes, to_unicode


class TestRequest:
    request_class = Request
    default_method = "GET"
    default_headers: dict[bytes, list[bytes]] = {}
    default_meta: dict[str, Any] = {}

    def test_init(self):
        # Request requires url in the __init__ method
        with pytest.raises(TypeError):
            self.request_class()

        # url argument must be basestring
        with pytest.raises(TypeError):
            self.request_class(123)
        r = self.request_class("http://www.example.com")

        r = self.request_class("http://www.example.com")
        assert isinstance(r.url, str)
        assert r.url == "http://www.example.com"
        assert r.method == self.default_method

        assert isinstance(r.headers, Headers)
        assert r.headers == self.default_headers
        assert r.meta == self.default_meta

        meta = {"lala": "lolo"}
        headers = {b"caca": b"coco"}
        r = self.request_class(
            "http://www.example.com", meta=meta, headers=headers, body="a body"
        )

        assert r.meta is not meta
        assert r.meta == meta
        assert r.headers is not headers
        assert r.headers[b"caca"] == b"coco"

    def test_url_scheme(self):
        # This test passes by not raising any (ValueError) exception
        self.request_class("http://example.org")
        self.request_class("https://example.org")
        self.request_class("s3://example.org")
        self.request_class("ftp://example.org")
        self.request_class("about:config")
        self.request_class("data:,Hello%2C%20World!")

    def test_url_no_scheme(self):
        msg = "Missing scheme in request url:"
        with pytest.raises(ValueError, match=msg):
            self.request_class("foo")
        with pytest.raises(ValueError, match=msg):
            self.request_class("/foo/")
        with pytest.raises(ValueError, match=msg):
            self.request_class("/foo:bar")

    def test_headers(self):
        # Different ways of setting headers attribute
        url = "http://www.scrapy.org"
        headers = {b"Accept": "gzip", b"Custom-Header": "nothing to tell you"}
        r = self.request_class(url=url, headers=headers)
        p = self.request_class(url=url, headers=r.headers)

        assert r.headers == p.headers
        assert r.headers is not headers
        assert p.headers is not r.headers

        # headers must not be unicode
        h = Headers({"key1": "val1", "key2": "val2"})
        h["newkey"] = "newval"
        for k, v in h.items():
            assert isinstance(k, bytes)
            for s in v:
                assert isinstance(s, bytes)

    def test_eq(self):
        url = "http://www.scrapy.org"
        r1 = self.request_class(url=url)
        r2 = self.request_class(url=url)
        assert r1 != r2

        set_ = set()
        set_.add(r1)
        set_.add(r2)
        assert len(set_) == 2

    def test_url(self):
        r = self.request_class(url="http://www.scrapy.org/path")
        assert r.url == "http://www.scrapy.org/path"

    def test_url_quoting(self):
        r = self.request_class(url="http://www.scrapy.org/blank%20space")
        assert r.url == "http://www.scrapy.org/blank%20space"
        r = self.request_class(url="http://www.scrapy.org/blank space")
        assert r.url == "http://www.scrapy.org/blank%20space"

    def test_url_encoding(self):
        r = self.request_class(url="http://www.scrapy.org/price/£")
        assert r.url == "http://www.scrapy.org/price/%C2%A3"

    def test_url_encoding_other(self):
        # encoding affects only query part of URI, not path
        # path part should always be UTF-8 encoded before percent-escaping
        r = self.request_class(url="http://www.scrapy.org/price/£", encoding="utf-8")
        assert r.url == "http://www.scrapy.org/price/%C2%A3"

        r = self.request_class(url="http://www.scrapy.org/price/£", encoding="latin1")
        assert r.url == "http://www.scrapy.org/price/%C2%A3"

    def test_url_encoding_query(self):
        r1 = self.request_class(url="http://www.scrapy.org/price/£?unit=µ")
        assert r1.url == "http://www.scrapy.org/price/%C2%A3?unit=%C2%B5"

        # should be same as above
        r2 = self.request_class(
            url="http://www.scrapy.org/price/£?unit=µ", encoding="utf-8"
        )
        assert r2.url == "http://www.scrapy.org/price/%C2%A3?unit=%C2%B5"

    def test_url_encoding_query_latin1(self):
        # encoding is used for encoding query-string before percent-escaping;
        # path is still UTF-8 encoded before percent-escaping
        r3 = self.request_class(
            url="http://www.scrapy.org/price/µ?currency=£", encoding="latin1"
        )
        assert r3.url == "http://www.scrapy.org/price/%C2%B5?currency=%A3"

    def test_url_encoding_nonutf8_untouched(self):
        # percent-escaping sequences that do not match valid UTF-8 sequences
        # should be kept untouched (just upper-cased perhaps)
        #
        # See https://datatracker.ietf.org/doc/html/rfc3987#section-3.2
        #
        # "Conversions from URIs to IRIs MUST NOT use any character encoding
        # other than UTF-8 in steps 3 and 4, even if it might be possible to
        # guess from the context that another character encoding than UTF-8 was
        # used in the URI.  For example, the URI
        # "http://www.example.org/r%E9sum%E9.html" might with some guessing be
        # interpreted to contain two e-acute characters encoded as iso-8859-1.
        # It must not be converted to an IRI containing these e-acute
        # characters.  Otherwise, in the future the IRI will be mapped to
        # "http://www.example.org/r%C3%A9sum%C3%A9.html", which is a different
        # URI from "http://www.example.org/r%E9sum%E9.html".
        r1 = self.request_class(url="http://www.scrapy.org/price/%a3")
        assert r1.url == "http://www.scrapy.org/price/%a3"

        r2 = self.request_class(url="http://www.scrapy.org/r%C3%A9sum%C3%A9/%a3")
        assert r2.url == "http://www.scrapy.org/r%C3%A9sum%C3%A9/%a3"

        r3 = self.request_class(url="http://www.scrapy.org/résumé/%a3")
        assert r3.url == "http://www.scrapy.org/r%C3%A9sum%C3%A9/%a3"

        r4 = self.request_class(url="http://www.example.org/r%E9sum%E9.html")
        assert r4.url == "http://www.example.org/r%E9sum%E9.html"

    def test_body(self):
        r1 = self.request_class(url="http://www.example.com/")
        assert r1.body == b""

        r2 = self.request_class(url="http://www.example.com/", body=b"")
        assert isinstance(r2.body, bytes)
        assert r2.encoding == "utf-8"  # default encoding

        r3 = self.request_class(
            url="http://www.example.com/", body="Price: \xa3100", encoding="utf-8"
        )
        assert isinstance(r3.body, bytes)
        assert r3.body == b"Price: \xc2\xa3100"

        r4 = self.request_class(
            url="http://www.example.com/", body="Price: \xa3100", encoding="latin1"
        )
        assert isinstance(r4.body, bytes)
        assert r4.body == b"Price: \xa3100"

    def test_copy(self):
        """Test Request copy"""

        def somecallback():
            pass

        r1 = self.request_class(
            "http://www.example.com",
            flags=["f1", "f2"],
            callback=somecallback,
            errback=somecallback,
        )
        r1.meta["foo"] = "bar"
        r1.cb_kwargs["key"] = "value"
        r2 = r1.copy()

        # make sure copy does not propagate callbacks
        assert r1.callback is somecallback
        assert r1.errback is somecallback
        assert r2.callback is r1.callback
        assert r2.errback is r2.errback

        # make sure flags list is shallow copied
        assert r1.flags is not r2.flags, "flags must be a shallow copy, not identical"
        assert r1.flags == r2.flags

        # make sure cb_kwargs dict is shallow copied
        assert r1.cb_kwargs is not r2.cb_kwargs, (
            "cb_kwargs must be a shallow copy, not identical"
        )
        assert r1.cb_kwargs == r2.cb_kwargs

        # make sure meta dict is shallow copied
        assert r1.meta is not r2.meta, "meta must be a shallow copy, not identical"
        assert r1.meta == r2.meta

        # make sure headers attribute is shallow copied
        assert r1.headers is not r2.headers, (
            "headers must be a shallow copy, not identical"
        )
        assert r1.headers == r2.headers
        assert r1.encoding == r2.encoding
        assert r1.dont_filter == r2.dont_filter

        # Request.body can be identical since it's an immutable object (str)

    def test_copy_inherited_classes(self):
        """Test Request children copies preserve their class"""

        class CustomRequest(self.request_class):
            pass

        r1 = CustomRequest("http://www.example.com")
        r2 = r1.copy()

        assert isinstance(r2, CustomRequest)

    def test_replace(self):
        """Test Request.replace() method"""
        r1 = self.request_class("http://www.example.com", method="GET")
        hdrs = Headers(r1.headers)
        hdrs[b"key"] = b"value"
        r2 = r1.replace(method="POST", body="New body", headers=hdrs)
        assert r1.url == r2.url
        assert (r1.method, r2.method) == ("GET", "POST")
        assert (r1.body, r2.body) == (b"", b"New body")
        assert (r1.headers, r2.headers) == (self.default_headers, hdrs)

        # Empty attributes (which may fail if not compared properly)
        r3 = self.request_class(
            "http://www.example.com", meta={"a": 1}, dont_filter=True
        )
        r4 = r3.replace(
            url="http://www.example.com/2", body=b"", meta={}, dont_filter=False
        )
        assert r4.url == "http://www.example.com/2"
        assert r4.body == b""
        assert r4.meta == {}
        assert r4.dont_filter is False

    def test_method_always_str(self):
        r = self.request_class("http://www.example.com", method="POST")
        assert isinstance(r.method, str)

    def test_immutable_attributes(self):
        r = self.request_class("http://example.com")
        with pytest.raises(AttributeError):
            r.url = "http://example2.com"
        with pytest.raises(AttributeError):
            r.body = "xxx"

    def test_callback_and_errback(self):
        def a_function():
            pass

        r1 = self.request_class("http://example.com")
        assert r1.callback is None
        assert r1.errback is None

        r2 = self.request_class("http://example.com", callback=a_function)
        assert r2.callback is a_function
        assert r2.errback is None

        r3 = self.request_class("http://example.com", errback=a_function)
        assert r3.callback is None
        assert r3.errback is a_function

        r4 = self.request_class(
            url="http://example.com",
            callback=a_function,
            errback=a_function,
        )
        assert r4.callback is a_function
        assert r4.errback is a_function

        r5 = self.request_class(
            url="http://example.com",
            callback=NO_CALLBACK,
            errback=NO_CALLBACK,
        )
        assert r5.callback is NO_CALLBACK
        assert r5.errback is NO_CALLBACK

    def test_callback_and_errback_type(self):
        with pytest.raises(TypeError):
            self.request_class("http://example.com", callback="a_function")
        with pytest.raises(TypeError):
            self.request_class("http://example.com", errback="a_function")
        with pytest.raises(TypeError):
            self.request_class(
                url="http://example.com",
                callback="a_function",
                errback="a_function",
            )

    def test_no_callback(self):
        with pytest.raises(RuntimeError):
            NO_CALLBACK()

    def test_from_curl(self):
        # Note: more curated tests regarding curl conversion are in
        # `test_utils_curl.py`
        curl_command = (
            "curl 'http://httpbin.org/post' -X POST -H 'Cookie: _gauges_unique"
            "_year=1; _gauges_unique=1; _gauges_unique_month=1; _gauges_unique"
            "_hour=1; _gauges_unique_day=1' -H 'Origin: http://httpbin.org' -H"
            " 'Accept-Encoding: gzip, deflate' -H 'Accept-Language: en-US,en;q"
            "=0.9,ru;q=0.8,es;q=0.7' -H 'Upgrade-Insecure-Requests: 1' -H 'Use"
            "r-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTM"
            "L, like Gecko) Ubuntu Chromium/62.0.3202.75 Chrome/62.0.3202.75 S"
            "afari/537.36' -H 'Content-Type: application /x-www-form-urlencode"
            "d' -H 'Accept: text/html,application/xhtml+xml,application/xml;q="
            "0.9,image/webp,image/apng,*/*;q=0.8' -H 'Cache-Control: max-age=0"
            "' -H 'Referer: http://httpbin.org/forms/post' -H 'Connection: kee"
            "p-alive' --data 'custname=John+Smith&custtel=500&custemail=jsmith"
            "%40example.org&size=small&topping=cheese&topping=onion&delivery=1"
            "2%3A15&comments=' --compressed"
        )
        r = self.request_class.from_curl(curl_command)
        assert r.method == "POST"
        assert r.url == "http://httpbin.org/post"
        assert (
            r.body == b"custname=John+Smith&custtel=500&custemail=jsmith%40"
            b"example.org&size=small&topping=cheese&topping=onion"
            b"&delivery=12%3A15&comments="
        )
        assert r.cookies == {
            "_gauges_unique_year": "1",
            "_gauges_unique": "1",
            "_gauges_unique_month": "1",
            "_gauges_unique_hour": "1",
            "_gauges_unique_day": "1",
        }
        assert r.headers == {
            b"Origin": [b"http://httpbin.org"],
            b"Accept-Encoding": [b"gzip, deflate"],
            b"Accept-Language": [b"en-US,en;q=0.9,ru;q=0.8,es;q=0.7"],
            b"Upgrade-Insecure-Requests": [b"1"],
            b"User-Agent": [
                b"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537."
                b"36 (KHTML, like Gecko) Ubuntu Chromium/62.0.3202"
                b".75 Chrome/62.0.3202.75 Safari/537.36"
            ],
            b"Content-Type": [b"application /x-www-form-urlencoded"],
            b"Accept": [
                b"text/html,application/xhtml+xml,application/xml;q=0."
                b"9,image/webp,image/apng,*/*;q=0.8"
            ],
            b"Cache-Control": [b"max-age=0"],
            b"Referer": [b"http://httpbin.org/forms/post"],
            b"Connection": [b"keep-alive"],
        }

    def test_from_curl_with_kwargs(self):
        r = self.request_class.from_curl(
            'curl -X PATCH "http://example.org"', method="POST", meta={"key": "value"}
        )
        assert r.method == "POST"
        assert r.meta == {"key": "value"}

    def test_from_curl_ignore_unknown_options(self):
        # By default: it works and ignores the unknown options: --foo and -z
        with warnings.catch_warnings():  # avoid warning when executing tests
            warnings.simplefilter("ignore")
            r = self.request_class.from_curl(
                'curl -X DELETE "http://example.org" --foo -z',
            )
            assert r.method == "DELETE"

        # If `ignore_unknown_options` is set to `False` it raises an error with
        # the unknown options: --foo and -z
        with pytest.raises(ValueError, match="Unrecognized options:"):
            self.request_class.from_curl(
                'curl -X PATCH "http://example.org" --foo -z',
                ignore_unknown_options=False,
            )


class TestFormRequest(TestRequest):
    request_class = FormRequest

    def assertQueryEqual(self, first, second, msg=None):
        first = to_unicode(first).split("&")
        second = to_unicode(second).split("&")
        assert sorted(first) == sorted(second), msg

    def test_empty_formdata(self):
        r1 = self.request_class("http://www.example.com", formdata={})
        assert r1.body == b""

    def test_formdata_overrides_querystring(self):
        data = (("a", "one"), ("a", "two"), ("b", "2"))
        url = self.request_class(
            "http://www.example.com/?a=0&b=1&c=3#fragment", method="GET", formdata=data
        ).url.split("#", maxsplit=1)[0]
        fs = _qs(self.request_class(url, method="GET", formdata=data))
        assert set(fs[b"a"]) == {b"one", b"two"}
        assert fs[b"b"] == [b"2"]
        assert fs.get(b"c") is None

        data = {"a": "1", "b": "2"}
        fs = _qs(
            self.request_class("http://www.example.com/", method="GET", formdata=data)
        )
        assert fs[b"a"] == [b"1"]
        assert fs[b"b"] == [b"2"]

    def test_default_encoding_bytes(self):
        # using default encoding (utf-8)
        data = {b"one": b"two", b"price": b"\xc2\xa3 100"}
        r2 = self.request_class("http://www.example.com", formdata=data)
        assert r2.method == "POST"
        assert r2.encoding == "utf-8"
        self.assertQueryEqual(r2.body, b"price=%C2%A3+100&one=two")
        assert r2.headers[b"Content-Type"] == b"application/x-www-form-urlencoded"

    def test_default_encoding_textual_data(self):
        # using default encoding (utf-8)
        data = {"µ one": "two", "price": "£ 100"}
        r2 = self.request_class("http://www.example.com", formdata=data)
        assert r2.method == "POST"
        assert r2.encoding == "utf-8"
        self.assertQueryEqual(r2.body, b"price=%C2%A3+100&%C2%B5+one=two")
        assert r2.headers[b"Content-Type"] == b"application/x-www-form-urlencoded"

    def test_default_encoding_mixed_data(self):
        # using default encoding (utf-8)
        data = {"\u00b5one": b"two", b"price\xc2\xa3": "\u00a3 100"}
        r2 = self.request_class("http://www.example.com", formdata=data)
        assert r2.method == "POST"
        assert r2.encoding == "utf-8"
        self.assertQueryEqual(r2.body, b"%C2%B5one=two&price%C2%A3=%C2%A3+100")
        assert r2.headers[b"Content-Type"] == b"application/x-www-form-urlencoded"

    def test_custom_encoding_bytes(self):
        data = {b"\xb5 one": b"two", b"price": b"\xa3 100"}
        r2 = self.request_class(
            "http://www.example.com", formdata=data, encoding="latin1"
        )
        assert r2.method == "POST"
        assert r2.encoding == "latin1"
        self.assertQueryEqual(r2.body, b"price=%A3+100&%B5+one=two")
        assert r2.headers[b"Content-Type"] == b"application/x-www-form-urlencoded"

    def test_custom_encoding_textual_data(self):
        data = {"price": "£ 100"}
        r3 = self.request_class(
            "http://www.example.com", formdata=data, encoding="latin1"
        )
        assert r3.encoding == "latin1"
        assert r3.body == b"price=%A3+100"

    def test_multi_key_values(self):
        # using multiples values for a single key
        data = {"price": "\xa3 100", "colours": ["red", "blue", "green"]}
        r3 = self.request_class("http://www.example.com", formdata=data)
        self.assertQueryEqual(
            r3.body, b"colours=red&colours=blue&colours=green&price=%C2%A3+100"
        )

    def test_from_response_post(self):
        response = _buildresponse(
            b"""<form action="post.php" method="POST">
            <input type="hidden" name="test" value="val1">
            <input type="hidden" name="test" value="val2">
            <input type="hidden" name="test2" value="xxx">
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(
            response, formdata={"one": ["two", "three"], "six": "seven"}
        )

        assert req.method == "POST"
        assert req.headers[b"Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req)
        assert set(fs[b"test"]) == {b"val1", b"val2"}
        assert set(fs[b"one"]) == {b"two", b"three"}
        assert fs[b"test2"] == [b"xxx"]
        assert fs[b"six"] == [b"seven"]

    def test_from_response_post_nonascii_bytes_utf8(self):
        response = _buildresponse(
            b"""<form action="post.php" method="POST">
            <input type="hidden" name="test \xc2\xa3" value="val1">
            <input type="hidden" name="test \xc2\xa3" value="val2">
            <input type="hidden" name="test2" value="xxx \xc2\xb5">
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(
            response, formdata={"one": ["two", "three"], "six": "seven"}
        )

        assert req.method == "POST"
        assert req.headers[b"Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req, to_unicode=True)
        assert set(fs["test £"]) == {"val1", "val2"}
        assert set(fs["one"]) == {"two", "three"}
        assert fs["test2"] == ["xxx µ"]
        assert fs["six"] == ["seven"]

    def test_from_response_post_nonascii_bytes_latin1(self):
        response = _buildresponse(
            b"""<form action="post.php" method="POST">
            <input type="hidden" name="test \xa3" value="val1">
            <input type="hidden" name="test \xa3" value="val2">
            <input type="hidden" name="test2" value="xxx \xb5">
            </form>""",
            url="http://www.example.com/this/list.html",
            encoding="latin1",
        )
        req = self.request_class.from_response(
            response, formdata={"one": ["two", "three"], "six": "seven"}
        )

        assert req.method == "POST"
        assert req.headers[b"Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req, to_unicode=True, encoding="latin1")
        assert set(fs["test £"]) == {"val1", "val2"}
        assert set(fs["one"]) == {"two", "three"}
        assert fs["test2"] == ["xxx µ"]
        assert fs["six"] == ["seven"]

    def test_from_response_post_nonascii_unicode(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test £" value="val1">
            <input type="hidden" name="test £" value="val2">
            <input type="hidden" name="test2" value="xxx µ">
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(
            response, formdata={"one": ["two", "three"], "six": "seven"}
        )

        assert req.method == "POST"
        assert req.headers[b"Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req, to_unicode=True)
        assert set(fs["test £"]) == {"val1", "val2"}
        assert set(fs["one"]) == {"two", "three"}
        assert fs["test2"] == ["xxx µ"]
        assert fs["six"] == ["seven"]

    def test_from_response_duplicate_form_key(self):
        response = _buildresponse("<form></form>", url="http://www.example.com")
        req = self.request_class.from_response(
            response=response,
            method="GET",
            formdata=(("foo", "bar"), ("foo", "baz")),
        )
        assert urlparse_cached(req).hostname == "www.example.com"
        assert urlparse_cached(req).query == "foo=bar&foo=baz"

    def test_from_response_override_duplicate_form_key(self):
        response = _buildresponse(
            """<form action="get.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            </form>"""
        )
        req = self.request_class.from_response(
            response, formdata=(("two", "2"), ("two", "4"))
        )
        fs = _qs(req)
        assert fs[b"one"] == [b"1"]
        assert fs[b"two"] == [b"2", b"4"]

    def test_from_response_extra_headers(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test" value="val1">
            <input type="hidden" name="test" value="val2">
            <input type="hidden" name="test2" value="xxx">
            </form>"""
        )
        req = self.request_class.from_response(
            response=response,
            formdata={"one": ["two", "three"], "six": "seven"},
            headers={"Accept-Encoding": "gzip,deflate"},
        )
        assert req.method == "POST"
        assert req.headers["Content-type"] == b"application/x-www-form-urlencoded"
        assert req.headers["Accept-Encoding"] == b"gzip,deflate"

    def test_from_response_get(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="hidden" name="test" value="val1">
            <input type="hidden" name="test" value="val2">
            <input type="hidden" name="test2" value="xxx">
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        r1 = self.request_class.from_response(
            response, formdata={"one": ["two", "three"], "six": "seven"}
        )
        assert r1.method == "GET"
        assert urlparse_cached(r1).hostname == "www.example.com"
        assert urlparse_cached(r1).path == "/this/get.php"
        fs = _qs(r1)
        assert set(fs[b"test"]) == {b"val1", b"val2"}
        assert set(fs[b"one"]) == {b"two", b"three"}
        assert fs[b"test2"] == [b"xxx"]
        assert fs[b"six"] == [b"seven"]

    def test_from_response_override_params(self):
        response = _buildresponse(
            """<form action="get.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            </form>"""
        )
        req = self.request_class.from_response(response, formdata={"two": "2"})
        fs = _qs(req)
        assert fs[b"one"] == [b"1"]
        assert fs[b"two"] == [b"2"]

    def test_from_response_drop_params(self):
        response = _buildresponse(
            """<form action="get.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            </form>"""
        )
        req = self.request_class.from_response(response, formdata={"two": None})
        fs = _qs(req)
        assert fs[b"one"] == [b"1"]
        assert b"two" not in fs

    def test_from_response_override_method(self):
        response = _buildresponse(
            """<html><body>
            <form action="/app"></form>
            </body></html>"""
        )
        request = FormRequest.from_response(response)
        assert request.method == "GET"
        request = FormRequest.from_response(response, method="POST")
        assert request.method == "POST"

    def test_from_response_override_url(self):
        response = _buildresponse(
            """<html><body>
            <form action="/app"></form>
            </body></html>"""
        )
        request = FormRequest.from_response(response)
        assert request.url == "http://example.com/app"
        request = FormRequest.from_response(response, url="http://foo.bar/absolute")
        assert request.url == "http://foo.bar/absolute"
        request = FormRequest.from_response(response, url="/relative")
        assert request.url == "http://example.com/relative"

    def test_from_response_case_insensitive(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="SuBmIt" name="clickable1" value="clicked1">
            <input type="iMaGe" name="i1" src="http://my.image.org/1.jpg">
            <input type="submit" name="clickable2" value="clicked2">
            </form>"""
        )
        req = self.request_class.from_response(response)
        fs = _qs(req)
        assert fs[b"clickable1"] == [b"clicked1"]
        assert b"i1" not in fs, fs  # xpath in _get_inputs()
        assert b"clickable2" not in fs, fs  # xpath in _get_clickable()

    def test_from_response_submit_first_clickable(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="clickable1" value="clicked1">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            <input type="submit" name="clickable2" value="clicked2">
            </form>"""
        )
        req = self.request_class.from_response(response, formdata={"two": "2"})
        fs = _qs(req)
        assert fs[b"clickable1"] == [b"clicked1"]
        assert b"clickable2" not in fs, fs
        assert fs[b"one"] == [b"1"]
        assert fs[b"two"] == [b"2"]

    def test_from_response_submit_not_first_clickable(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="clickable1" value="clicked1">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            <input type="submit" name="clickable2" value="clicked2">
            </form>"""
        )
        req = self.request_class.from_response(
            response, formdata={"two": "2"}, clickdata={"name": "clickable2"}
        )
        fs = _qs(req)
        assert fs[b"clickable2"] == [b"clicked2"]
        assert b"clickable1" not in fs, fs
        assert fs[b"one"] == [b"1"]
        assert fs[b"two"] == [b"2"]

    def test_from_response_dont_submit_image_as_input(self):
        response = _buildresponse(
            """<form>
            <input type="hidden" name="i1" value="i1v">
            <input type="image" name="i2" src="http://my.image.org/1.jpg">
            <input type="submit" name="i3" value="i3v">
            </form>"""
        )
        req = self.request_class.from_response(response, dont_click=True)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v"]}

    def test_from_response_dont_submit_reset_as_input(self):
        response = _buildresponse(
            """<form>
            <input type="hidden" name="i1" value="i1v">
            <input type="text" name="i2" value="i2v">
            <input type="reset" name="resetme">
            <input type="submit" name="i3" value="i3v">
            </form>"""
        )
        req = self.request_class.from_response(response, dont_click=True)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v"], b"i2": [b"i2v"]}

    def test_from_response_clickdata_does_not_ignore_image(self):
        response = _buildresponse(
            """<form>
            <input type="text" name="i1" value="i1v">
            <input id="image" name="i2" type="image" value="i2v" alt="Login" src="http://my.image.org/1.jpg">
            </form>"""
        )
        req = self.request_class.from_response(response)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v"], b"i2": [b"i2v"]}

    def test_from_response_multiple_clickdata(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="clickable" value="clicked1">
            <input type="submit" name="clickable" value="clicked2">
            <input type="hidden" name="one" value="clicked1">
            <input type="hidden" name="two" value="clicked2">
            </form>"""
        )
        req = self.request_class.from_response(
            response, clickdata={"name": "clickable", "value": "clicked2"}
        )
        fs = _qs(req)
        assert fs[b"clickable"] == [b"clicked2"]
        assert fs[b"one"] == [b"clicked1"]
        assert fs[b"two"] == [b"clicked2"]

    def test_from_response_unicode_clickdata(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="price in \u00a3" value="\u00a3 1000">
            <input type="submit" name="price in \u20ac" value="\u20ac 2000">
            <input type="hidden" name="poundsign" value="\u00a3">
            <input type="hidden" name="eurosign" value="\u20ac">
            </form>"""
        )
        req = self.request_class.from_response(
            response, clickdata={"name": "price in \u00a3"}
        )
        fs = _qs(req, to_unicode=True)
        assert fs["price in \u00a3"]

    def test_from_response_unicode_clickdata_latin1(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="price in \u00a3" value="\u00a3 1000">
            <input type="submit" name="price in \u00a5" value="\u00a5 2000">
            <input type="hidden" name="poundsign" value="\u00a3">
            <input type="hidden" name="yensign" value="\u00a5">
            </form>""",
            encoding="latin1",
        )
        req = self.request_class.from_response(
            response, clickdata={"name": "price in \u00a5"}
        )
        fs = _qs(req, to_unicode=True, encoding="latin1")
        assert fs["price in \u00a5"]

    def test_from_response_multiple_forms_clickdata(self):
        response = _buildresponse(
            """<form name="form1">
            <input type="submit" name="clickable" value="clicked1">
            <input type="hidden" name="field1" value="value1">
            </form>
            <form name="form2">
            <input type="submit" name="clickable" value="clicked2">
            <input type="hidden" name="field2" value="value2">
            </form>
            """
        )
        req = self.request_class.from_response(
            response, formname="form2", clickdata={"name": "clickable"}
        )
        fs = _qs(req)
        assert fs[b"clickable"] == [b"clicked2"]
        assert fs[b"field2"] == [b"value2"]
        assert b"field1" not in fs, fs

    def test_from_response_override_clickable(self):
        response = _buildresponse(
            """<form><input type="submit" name="clickme" value="one"> </form>"""
        )
        req = self.request_class.from_response(
            response, formdata={"clickme": "two"}, clickdata={"name": "clickme"}
        )
        fs = _qs(req)
        assert fs[b"clickme"] == [b"two"]

    def test_from_response_dont_click(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="submit" name="clickable1" value="clicked1">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            <input type="submit" name="clickable2" value="clicked2">
            </form>"""
        )
        r1 = self.request_class.from_response(response, dont_click=True)
        fs = _qs(r1)
        assert b"clickable1" not in fs, fs
        assert b"clickable2" not in fs, fs

    def test_from_response_ambiguous_clickdata(self):
        response = _buildresponse(
            """
            <form action="get.php" method="GET">
            <input type="submit" name="clickable1" value="clicked1">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="3">
            <input type="submit" name="clickable2" value="clicked2">
            </form>"""
        )
        with pytest.raises(
            ValueError,
            match="Multiple elements found .* matching the criteria in clickdata",
        ):
            self.request_class.from_response(response, clickdata={"type": "submit"})

    def test_from_response_non_matching_clickdata(self):
        response = _buildresponse(
            """<form>
            <input type="submit" name="clickable" value="clicked">
            </form>"""
        )
        with pytest.raises(
            ValueError, match="No clickable element matching clickdata:"
        ):
            self.request_class.from_response(
                response, clickdata={"nonexistent": "notme"}
            )

    def test_from_response_nr_index_clickdata(self):
        response = _buildresponse(
            """<form>
            <input type="submit" name="clickable1" value="clicked1">
            <input type="submit" name="clickable2" value="clicked2">
            </form>
            """
        )
        req = self.request_class.from_response(response, clickdata={"nr": 1})
        fs = _qs(req)
        assert b"clickable2" in fs
        assert b"clickable1" not in fs

    def test_from_response_invalid_nr_index_clickdata(self):
        response = _buildresponse(
            """<form>
            <input type="submit" name="clickable" value="clicked">
            </form>
            """
        )
        with pytest.raises(
            ValueError, match="No clickable element matching clickdata:"
        ):
            self.request_class.from_response(response, clickdata={"nr": 1})

    def test_from_response_errors_noform(self):
        response = _buildresponse("""<html></html>""")
        with pytest.raises(ValueError, match="No <form> element found in"):
            self.request_class.from_response(response)

    def test_from_response_invalid_html5(self):
        response = _buildresponse(
            """<!DOCTYPE html><body></html><form>"""
            """<input type="text" name="foo" value="xxx">"""
            """</form></body></html>"""
        )
        req = self.request_class.from_response(response, formdata={"bar": "buz"})
        fs = _qs(req)
        assert fs == {b"foo": [b"xxx"], b"bar": [b"buz"]}

    def test_from_response_errors_formnumber(self):
        response = _buildresponse(
            """<form action="get.php" method="GET">
            <input type="hidden" name="test" value="val1">
            <input type="hidden" name="test" value="val2">
            <input type="hidden" name="test2" value="xxx">
            </form>"""
        )
        with pytest.raises(IndexError):
            self.request_class.from_response(response, formnumber=1)

    def test_from_response_noformname(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>"""
        )
        r1 = self.request_class.from_response(response, formdata={"two": "3"})
        assert r1.method == "POST"
        assert r1.headers["Content-type"] == b"application/x-www-form-urlencoded"
        fs = _qs(r1)
        assert fs == {b"one": [b"1"], b"two": [b"3"]}

    def test_from_response_formname_exists(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>
            <form name="form2" action="post.php" method="POST">
            <input type="hidden" name="three" value="3">
            <input type="hidden" name="four" value="4">
            </form>"""
        )
        r1 = self.request_class.from_response(response, formname="form2")
        assert r1.method == "POST"
        fs = _qs(r1)
        assert fs == {b"four": [b"4"], b"three": [b"3"]}

    def test_from_response_formname_nonexistent(self):
        response = _buildresponse(
            """<form name="form1" action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            </form>
            <form name="form2" action="post.php" method="POST">
            <input type="hidden" name="two" value="2">
            </form>"""
        )
        r1 = self.request_class.from_response(response, formname="form3")
        assert r1.method == "POST"
        fs = _qs(r1)
        assert fs == {b"one": [b"1"]}

    def test_from_response_formname_errors_formnumber(self):
        response = _buildresponse(
            """<form name="form1" action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            </form>
            <form name="form2" action="post.php" method="POST">
            <input type="hidden" name="two" value="2">
            </form>"""
        )
        with pytest.raises(IndexError):
            self.request_class.from_response(response, formname="form3", formnumber=2)

    def test_from_response_formid_exists(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>
            <form id="form2" action="post.php" method="POST">
            <input type="hidden" name="three" value="3">
            <input type="hidden" name="four" value="4">
            </form>"""
        )
        r1 = self.request_class.from_response(response, formid="form2")
        assert r1.method == "POST"
        fs = _qs(r1)
        assert fs == {b"four": [b"4"], b"three": [b"3"]}

    def test_from_response_formname_nonexistent_fallback_formid(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>
            <form id="form2" name="form2" action="post.php" method="POST">
            <input type="hidden" name="three" value="3">
            <input type="hidden" name="four" value="4">
            </form>"""
        )
        r1 = self.request_class.from_response(
            response, formname="form3", formid="form2"
        )
        assert r1.method == "POST"
        fs = _qs(r1)
        assert fs == {b"four": [b"4"], b"three": [b"3"]}

    def test_from_response_formid_nonexistent(self):
        response = _buildresponse(
            """<form id="form1" action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            </form>
            <form id="form2" action="post.php" method="POST">
            <input type="hidden" name="two" value="2">
            </form>"""
        )
        r1 = self.request_class.from_response(response, formid="form3")
        assert r1.method == "POST"
        fs = _qs(r1)
        assert fs == {b"one": [b"1"]}

    def test_from_response_formid_errors_formnumber(self):
        response = _buildresponse(
            """<form id="form1" action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            </form>
            <form id="form2" name="form2" action="post.php" method="POST">
            <input type="hidden" name="two" value="2">
            </form>"""
        )
        with pytest.raises(IndexError):
            self.request_class.from_response(response, formid="form3", formnumber=2)

    def test_from_response_select(self):
        res = _buildresponse(
            """<form>
            <select name="i1">
                <option value="i1v1">option 1</option>
                <option value="i1v2" selected>option 2</option>
            </select>
            <select name="i2">
                <option value="i2v1">option 1</option>
                <option value="i2v2">option 2</option>
            </select>
            <select>
                <option value="i3v1">option 1</option>
                <option value="i3v2">option 2</option>
            </select>
            <select name="i4" multiple>
                <option value="i4v1">option 1</option>
                <option value="i4v2" selected>option 2</option>
                <option value="i4v3" selected>option 3</option>
            </select>
            <select name="i5" multiple>
                <option value="i5v1">option 1</option>
                <option value="i5v2">option 2</option>
            </select>
            <select name="i6"></select>
            <select name="i7"/>
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req, to_unicode=True)
        assert fs == {"i1": ["i1v2"], "i2": ["i2v1"], "i4": ["i4v2", "i4v3"]}

    def test_from_response_radio(self):
        res = _buildresponse(
            """<form>
            <input type="radio" name="i1" value="i1v1">
            <input type="radio" name="i1" value="iv2" checked>
            <input type="radio" name="i2" checked>
            <input type="radio" name="i2">
            <input type="radio" name="i3" value="i3v1">
            <input type="radio" name="i3">
            <input type="radio" value="i4v1">
            <input type="radio">
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert fs == {b"i1": [b"iv2"], b"i2": [b"on"]}

    def test_from_response_checkbox(self):
        res = _buildresponse(
            """<form>
            <input type="checkbox" name="i1" value="i1v1">
            <input type="checkbox" name="i1" value="iv2" checked>
            <input type="checkbox" name="i2" checked>
            <input type="checkbox" name="i2">
            <input type="checkbox" name="i3" value="i3v1">
            <input type="checkbox" name="i3">
            <input type="checkbox" value="i4v1">
            <input type="checkbox">
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert fs == {b"i1": [b"iv2"], b"i2": [b"on"]}

    def test_from_response_input_text(self):
        res = _buildresponse(
            """<form>
            <input type="text" name="i1" value="i1v1">
            <input type="text" name="i2">
            <input type="text" value="i3v1">
            <input type="text">
            <input name="i4" value="i4v1">
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v1"], b"i2": [b""], b"i4": [b"i4v1"]}

    def test_from_response_input_hidden(self):
        res = _buildresponse(
            """<form>
            <input type="hidden" name="i1" value="i1v1">
            <input type="hidden" name="i2">
            <input type="hidden" value="i3v1">
            <input type="hidden">
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v1"], b"i2": [b""]}

    def test_from_response_input_textarea(self):
        res = _buildresponse(
            """<form>
            <textarea name="i1">i1v</textarea>
            <textarea name="i2"></textarea>
            <textarea name="i3"/>
            <textarea>i4v</textarea>
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert fs == {b"i1": [b"i1v"], b"i2": [b""], b"i3": [b""]}

    def test_from_response_descendants(self):
        res = _buildresponse(
            """<form>
            <div>
              <fieldset>
                <input type="text" name="i1">
                <select name="i2">
                    <option value="v1" selected>
                </select>
              </fieldset>
              <input type="radio" name="i3" value="i3v2" checked>
              <input type="checkbox" name="i4" value="i4v2" checked>
              <textarea name="i5"></textarea>
              <input type="hidden" name="h1" value="h1v">
              </div>
            <input type="hidden" name="h2" value="h2v">
            </form>"""
        )
        req = self.request_class.from_response(res)
        fs = _qs(req)
        assert set(fs) == {b"h2", b"i2", b"i1", b"i3", b"h1", b"i5", b"i4"}

    def test_from_response_xpath(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>
            <form action="post2.php" method="POST">
            <input type="hidden" name="three" value="3">
            <input type="hidden" name="four" value="4">
            </form>"""
        )
        r1 = self.request_class.from_response(
            response, formxpath="//form[@action='post.php']"
        )
        fs = _qs(r1)
        assert fs[b"one"] == [b"1"]

        r1 = self.request_class.from_response(
            response, formxpath="//form/input[@name='four']"
        )
        fs = _qs(r1)
        assert fs[b"three"] == [b"3"]

        with pytest.raises(ValueError, match="No <form> element found with"):
            self.request_class.from_response(
                response, formxpath="//form/input[@name='abc']"
            )

    def test_from_response_unicode_xpath(self):
        response = _buildresponse(b'<form name="\xd1\x8a"></form>')
        r = self.request_class.from_response(
            response, formxpath="//form[@name='\u044a']"
        )
        fs = _qs(r)
        assert not fs

        xpath = "//form[@name='\u03b1']"
        with pytest.raises(ValueError, match=re.escape(xpath)):
            self.request_class.from_response(response, formxpath=xpath)

    def test_from_response_button_submit(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test1" value="val1">
            <input type="hidden" name="test2" value="val2">
            <button type="submit" name="button1" value="submit1">Submit</button>
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(response)
        assert req.method == "POST"
        assert req.headers["Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req)
        assert fs[b"test1"] == [b"val1"]
        assert fs[b"test2"] == [b"val2"]
        assert fs[b"button1"] == [b"submit1"]

    def test_from_response_button_notype(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test1" value="val1">
            <input type="hidden" name="test2" value="val2">
            <button name="button1" value="submit1">Submit</button>
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(response)
        assert req.method == "POST"
        assert req.headers["Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req)
        assert fs[b"test1"] == [b"val1"]
        assert fs[b"test2"] == [b"val2"]
        assert fs[b"button1"] == [b"submit1"]

    def test_from_response_submit_novalue(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test1" value="val1">
            <input type="hidden" name="test2" value="val2">
            <input type="submit" name="button1">Submit</button>
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(response)
        assert req.method == "POST"
        assert req.headers["Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req)
        assert fs[b"test1"] == [b"val1"]
        assert fs[b"test2"] == [b"val2"]
        assert fs[b"button1"] == [b""]

    def test_from_response_button_novalue(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="test1" value="val1">
            <input type="hidden" name="test2" value="val2">
            <button type="submit" name="button1">Submit</button>
            </form>""",
            url="http://www.example.com/this/list.html",
        )
        req = self.request_class.from_response(response)
        assert req.method == "POST"
        assert req.headers["Content-type"] == b"application/x-www-form-urlencoded"
        assert req.url == "http://www.example.com/this/post.php"
        fs = _qs(req)
        assert fs[b"test1"] == [b"val1"]
        assert fs[b"test2"] == [b"val2"]
        assert fs[b"button1"] == [b""]

    def test_html_base_form_action(self):
        response = _buildresponse(
            """
            <html>
                <head>
                    <base href=" http://b.com/">
                </head>
                <body>
                    <form action="test_form">
                    </form>
                </body>
            </html>
            """,
            url="http://a.com/",
        )
        req = self.request_class.from_response(response)
        assert req.url == "http://b.com/test_form"

    def test_spaces_in_action(self):
        resp = _buildresponse('<body><form action=" path\n"></form></body>')
        req = self.request_class.from_response(resp)
        assert req.url == "http://example.com/path"

    def test_from_response_css(self):
        response = _buildresponse(
            """<form action="post.php" method="POST">
            <input type="hidden" name="one" value="1">
            <input type="hidden" name="two" value="2">
            </form>
            <form action="post2.php" method="POST">
            <input type="hidden" name="three" value="3">
            <input type="hidden" name="four" value="4">
            </form>"""
        )
        r1 = self.request_class.from_response(
            response, formcss="form[action='post.php']"
        )
        fs = _qs(r1)
        assert fs[b"one"] == [b"1"]

        r1 = self.request_class.from_response(response, formcss="input[name='four']")
        fs = _qs(r1)
        assert fs[b"three"] == [b"3"]

        with pytest.raises(ValueError, match="No <form> element found with"):
            self.request_class.from_response(response, formcss="input[name='abc']")

    def test_from_response_valid_form_methods(self):
        form_methods = [
            [method, method] for method in self.request_class.valid_form_methods
        ]
        form_methods.append(["UNKNOWN", "GET"])

        for method, expected in form_methods:
            response = _buildresponse(
                f'<form action="post.php" method="{method}">'
                '<input type="hidden" name="one" value="1">'
                "</form>"
            )
            r = self.request_class.from_response(response)
            assert r.method == expected

    def test_form_response_with_invalid_formdata_type_error(self):
        """Test that a ValueError is raised for non-iterable and non-dict formdata input"""
        response = _buildresponse(
            """<html><body>
            <form action="/submit" method="post">
                <input type="text" name="test" value="value">
            </form>
            </body></html>"""
        )
        with pytest.raises(
            ValueError, match="formdata should be a dict or iterable of tuples"
        ):
            FormRequest.from_response(response, formdata=123)

    def test_form_response_with_custom_invalid_formdata_value_error(self):
        """Test that a ValueError is raised for fault-inducing iterable formdata input"""
        response = _buildresponse(
            """<html><body>
                <form action="/submit" method="post">
                    <input type="text" name="test" value="value">
                </form>
            </body></html>"""
        )

        with pytest.raises(
            ValueError, match="formdata should be a dict or iterable of tuples"
        ):
            FormRequest.from_response(response, formdata=("a",))

    def test_get_form_with_xpath_no_form_parent(self):
        """Test that _get_from raised a ValueError when an XPath selects an element
        not nested within a <form> and no <form> parent is found"""
        response = _buildresponse(
            """<html><body>
                <div id="outside-form">
                    <p>This paragraph is not inside a form.</p>
                </div>
                <form action="/submit" method="post">
                    <input type="text" name="inside-form" value="">
                </form>
            </body></html>"""
        )

        with pytest.raises(ValueError, match="No <form> element found with"):
            FormRequest.from_response(response, formxpath='//div[@id="outside-form"]/p')


def _buildresponse(body, **kwargs):
    kwargs.setdefault("body", body)
    kwargs.setdefault("url", "http://example.com")
    kwargs.setdefault("encoding", "utf-8")
    return HtmlResponse(**kwargs)


def _qs(req, encoding="utf-8", to_unicode=False):
    qs = req.body if req.method == "POST" else req.url.partition("?")[2]
    uqs = unquote_to_bytes(qs)
    if to_unicode:
        uqs = uqs.decode(encoding)
    return parse_qs(uqs, True)


class TestXmlRpcRequest(TestRequest):
    request_class = XmlRpcRequest
    default_method = "POST"
    default_headers = {b"Content-Type": [b"text/xml"]}

    def _test_request(self, **kwargs):
        r = self.request_class("http://scrapytest.org/rpc2", **kwargs)
        assert r.headers[b"Content-Type"] == b"text/xml"
        assert r.body == to_bytes(
            xmlrpc.client.dumps(**kwargs), encoding=kwargs.get("encoding", "utf-8")
        )
        assert r.method == "POST"
        assert r.encoding == kwargs.get("encoding", "utf-8")
        assert r.dont_filter

    def test_xmlrpc_dumps(self):
        self._test_request(params=("value",))
        self._test_request(params=("username", "password"), methodname="login")
        self._test_request(params=("response",), methodresponse="login")
        self._test_request(params=("pas£",), encoding="utf-8")
        self._test_request(params=(None,), allow_none=1)
        with pytest.raises(TypeError):
            self._test_request()
        with pytest.raises(TypeError):
            self._test_request(params=(None,))

    def test_latin1(self):
        self._test_request(params=("pas£",), encoding="latin1")


class TestJsonRequest(TestRequest):
    request_class = JsonRequest
    default_method = "GET"
    default_headers = {
        b"Content-Type": [b"application/json"],
        b"Accept": [b"application/json, text/javascript, */*; q=0.01"],
    }

    def test_data(self):
        r1 = self.request_class(url="http://www.example.com/")
        assert r1.body == b""

        body = b"body"
        r2 = self.request_class(url="http://www.example.com/", body=body)
        assert r2.body == body

        data = {
            "name": "value",
        }
        r3 = self.request_class(url="http://www.example.com/", data=data)
        assert r3.body == to_bytes(json.dumps(data))

        # empty data
        r4 = self.request_class(url="http://www.example.com/", data=[])
        assert r4.body == to_bytes(json.dumps([]))

    def test_data_method(self):
        # data is not passed
        r1 = self.request_class(url="http://www.example.com/")
        assert r1.method == "GET"

        body = b"body"
        r2 = self.request_class(url="http://www.example.com/", body=body)
        assert r2.method == "GET"

        data = {
            "name": "value",
        }
        r3 = self.request_class(url="http://www.example.com/", data=data)
        assert r3.method == "POST"

        # method passed explicitly
        r4 = self.request_class(url="http://www.example.com/", data=data, method="GET")
        assert r4.method == "GET"

        r5 = self.request_class(url="http://www.example.com/", data=[])
        assert r5.method == "POST"

    def test_body_data(self):
        """passing both body and data should result a warning"""
        body = b"body"
        data = {
            "name": "value",
        }
        with warnings.catch_warnings(record=True) as _warnings:
            r5 = self.request_class(url="http://www.example.com/", body=body, data=data)
            assert r5.body == body
            assert r5.method == "GET"
            assert len(_warnings) == 1
            assert "data will be ignored" in str(_warnings[0].message)

    def test_empty_body_data(self):
        """passing any body value and data should result a warning"""
        data = {
            "name": "value",
        }
        with warnings.catch_warnings(record=True) as _warnings:
            r6 = self.request_class(url="http://www.example.com/", body=b"", data=data)
            assert r6.body == b""
            assert r6.method == "GET"
            assert len(_warnings) == 1
            assert "data will be ignored" in str(_warnings[0].message)

    def test_body_none_data(self):
        data = {
            "name": "value",
        }
        with warnings.catch_warnings(record=True) as _warnings:
            r7 = self.request_class(url="http://www.example.com/", body=None, data=data)
            assert r7.body == to_bytes(json.dumps(data))
            assert r7.method == "POST"
            assert len(_warnings) == 0

    def test_body_data_none(self):
        with warnings.catch_warnings(record=True) as _warnings:
            r8 = self.request_class(url="http://www.example.com/", body=None, data=None)
            assert r8.method == "GET"
            assert len(_warnings) == 0

    def test_dumps_sort_keys(self):
        """Test that sort_keys=True is passed to json.dumps by default"""
        data = {
            "name": "value",
        }
        with mock.patch("json.dumps", return_value=b"") as mock_dumps:
            self.request_class(url="http://www.example.com/", data=data)
            kwargs = mock_dumps.call_args[1]
            assert kwargs["sort_keys"] is True

    def test_dumps_kwargs(self):
        """Test that dumps_kwargs are passed to json.dumps"""
        data = {
            "name": "value",
        }
        dumps_kwargs = {
            "ensure_ascii": True,
            "allow_nan": True,
        }
        with mock.patch("json.dumps", return_value=b"") as mock_dumps:
            self.request_class(
                url="http://www.example.com/", data=data, dumps_kwargs=dumps_kwargs
            )
            kwargs = mock_dumps.call_args[1]
            assert kwargs["ensure_ascii"] is True
            assert kwargs["allow_nan"] is True

    def test_replace_data(self):
        data1 = {
            "name1": "value1",
        }
        data2 = {
            "name2": "value2",
        }
        r1 = self.request_class(url="http://www.example.com/", data=data1)
        r2 = r1.replace(data=data2)
        assert r2.body == to_bytes(json.dumps(data2))

    def test_replace_sort_keys(self):
        """Test that replace provides sort_keys=True to json.dumps"""
        data1 = {
            "name1": "value1",
        }
        data2 = {
            "name2": "value2",
        }
        r1 = self.request_class(url="http://www.example.com/", data=data1)
        with mock.patch("json.dumps", return_value=b"") as mock_dumps:
            r1.replace(data=data2)
            kwargs = mock_dumps.call_args[1]
            assert kwargs["sort_keys"] is True

    def test_replace_dumps_kwargs(self):
        """Test that dumps_kwargs are provided to json.dumps when replace is called"""
        data1 = {
            "name1": "value1",
        }
        data2 = {
            "name2": "value2",
        }
        dumps_kwargs = {
            "ensure_ascii": True,
            "allow_nan": True,
        }
        r1 = self.request_class(
            url="http://www.example.com/", data=data1, dumps_kwargs=dumps_kwargs
        )
        with mock.patch("json.dumps", return_value=b"") as mock_dumps:
            r1.replace(data=data2)
            kwargs = mock_dumps.call_args[1]
            assert kwargs["ensure_ascii"] is True
            assert kwargs["allow_nan"] is True

    def test_replacement_both_body_and_data_warns(self):
        """Test that we get a warning if both body and data are passed"""
        body1 = None
        body2 = b"body"
        data1 = {
            "name1": "value1",
        }
        data2 = {
            "name2": "value2",
        }
        r1 = self.request_class(url="http://www.example.com/", data=data1, body=body1)

        with warnings.catch_warnings(record=True) as _warnings:
            r1.replace(data=data2, body=body2)
            assert "Both body and data passed. data will be ignored" in str(
                _warnings[0].message
            )
