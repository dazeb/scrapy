import asyncio
from gzip import BadGzipFile
from unittest import mock

import pytest
from twisted.internet import defer
from twisted.internet.defer import Deferred
from twisted.python.failure import Failure
from twisted.trial.unittest import TestCase

from scrapy.core.downloader.middleware import DownloaderMiddlewareManager
from scrapy.exceptions import _InvalidOutput
from scrapy.http import Request, Response
from scrapy.spiders import Spider
from scrapy.utils.python import to_bytes
from scrapy.utils.test import get_crawler, get_from_asyncio_queue


class TestManagerBase(TestCase):
    settings_dict = None

    def setUp(self):
        self.crawler = get_crawler(Spider, self.settings_dict)
        self.spider = self.crawler._create_spider("foo")
        self.mwman = DownloaderMiddlewareManager.from_crawler(self.crawler)
        self.crawler.engine = self.crawler._create_engine()
        return self.crawler.engine.open_spider(self.spider, start_requests=())

    def tearDown(self):
        return self.crawler.engine.close_spider(self.spider)

    def _download(self, request, response=None):
        """Executes downloader mw manager's download method and returns
        the result (Request or Response) or raise exception in case of
        failure.
        """
        if not response:
            response = Response(request.url)

        def download_func(request, spider):
            return response

        dfd = self.mwman.download(download_func, request, self.spider)
        # catch deferred result and return the value
        results = []
        dfd.addBoth(results.append)
        self._wait(dfd)
        ret = results[0]
        if isinstance(ret, Failure):
            ret.raiseException()
        return ret


class TestDefaults(TestManagerBase):
    """Tests default behavior with default settings"""

    def test_request_response(self):
        req = Request("http://example.com/index.html")
        resp = Response(req.url, status=200)
        ret = self._download(req, resp)
        assert isinstance(ret, Response), "Non-response returned"

    def test_3xx_and_invalid_gzipped_body_must_redirect(self):
        """Regression test for a failure when redirecting a compressed
        request.

        This happens when httpcompression middleware is executed before redirect
        middleware and attempts to decompress a non-compressed body.
        In particular when some website returns a 30x response with header
        'Content-Encoding: gzip' giving as result the error below:

            BadGzipFile: Not a gzipped file (...)

        """
        req = Request("http://example.com")
        body = b"<p>You are being redirected</p>"
        resp = Response(
            req.url,
            status=302,
            body=body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "text/html",
                "Content-Encoding": "gzip",
                "Location": "http://example.com/login",
            },
        )
        ret = self._download(request=req, response=resp)
        assert isinstance(ret, Request), f"Not redirected: {ret!r}"
        assert to_bytes(ret.url) == resp.headers["Location"], (
            "Not redirected to location header"
        )

    def test_200_and_invalid_gzipped_body_must_fail(self):
        req = Request("http://example.com")
        body = b"<p>You are being redirected</p>"
        resp = Response(
            req.url,
            status=200,
            body=body,
            headers={
                "Content-Length": str(len(body)),
                "Content-Type": "text/html",
                "Content-Encoding": "gzip",
                "Location": "http://example.com/login",
            },
        )
        with pytest.raises(BadGzipFile):
            self._download(request=req, response=resp)


class TestResponseFromProcessRequest(TestManagerBase):
    """Tests middleware returning a response from process_request."""

    def test_download_func_not_called(self):
        resp = Response("http://example.com/index.html")

        class ResponseMiddleware:
            def process_request(self, request, spider):
                return resp

        self.mwman._add_middleware(ResponseMiddleware())

        req = Request("http://example.com/index.html")
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        self._wait(dfd)

        assert results[0] is resp
        assert not download_func.called


class TestProcessRequestInvalidOutput(TestManagerBase):
    """Invalid return value for process_request method should raise an exception"""

    def test_invalid_process_request(self):
        req = Request("http://example.com/index.html")

        class InvalidProcessRequestMiddleware:
            def process_request(self, request, spider):
                return 1

        self.mwman._add_middleware(InvalidProcessRequestMiddleware())
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        assert isinstance(results[0], Failure)
        assert isinstance(results[0].value, _InvalidOutput)


class TestProcessResponseInvalidOutput(TestManagerBase):
    """Invalid return value for process_response method should raise an exception"""

    def test_invalid_process_response(self):
        req = Request("http://example.com/index.html")

        class InvalidProcessResponseMiddleware:
            def process_response(self, request, response, spider):
                return 1

        self.mwman._add_middleware(InvalidProcessResponseMiddleware())
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        assert isinstance(results[0], Failure)
        assert isinstance(results[0].value, _InvalidOutput)


class TestProcessExceptionInvalidOutput(TestManagerBase):
    """Invalid return value for process_exception method should raise an exception"""

    def test_invalid_process_exception(self):
        req = Request("http://example.com/index.html")

        class InvalidProcessExceptionMiddleware:
            def process_request(self, request, spider):
                raise RuntimeError

            def process_exception(self, request, exception, spider):
                return 1

        self.mwman._add_middleware(InvalidProcessExceptionMiddleware())
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        assert isinstance(results[0], Failure)
        assert isinstance(results[0].value, _InvalidOutput)


class TestMiddlewareUsingDeferreds(TestManagerBase):
    """Middlewares using Deferreds should work"""

    def test_deferred(self):
        resp = Response("http://example.com/index.html")

        class DeferredMiddleware:
            def cb(self, result):
                return result

            def process_request(self, request, spider):
                d = Deferred()
                d.addCallback(self.cb)
                d.callback(resp)
                return d

        self.mwman._add_middleware(DeferredMiddleware())
        req = Request("http://example.com/index.html")
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        self._wait(dfd)

        assert results[0] is resp
        assert not download_func.called


@pytest.mark.usefixtures("reactor_pytest")
class TestMiddlewareUsingCoro(TestManagerBase):
    """Middlewares using asyncio coroutines should work"""

    def test_asyncdef(self):
        resp = Response("http://example.com/index.html")

        class CoroMiddleware:
            async def process_request(self, request, spider):
                await defer.succeed(42)
                return resp

        self.mwman._add_middleware(CoroMiddleware())
        req = Request("http://example.com/index.html")
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        self._wait(dfd)

        assert results[0] is resp
        assert not download_func.called

    @pytest.mark.only_asyncio
    def test_asyncdef_asyncio(self):
        resp = Response("http://example.com/index.html")

        class CoroMiddleware:
            async def process_request(self, request, spider):
                await asyncio.sleep(0.1)
                return await get_from_asyncio_queue(resp)

        self.mwman._add_middleware(CoroMiddleware())
        req = Request("http://example.com/index.html")
        download_func = mock.MagicMock()
        dfd = self.mwman.download(download_func, req, self.spider)
        results = []
        dfd.addBoth(results.append)
        self._wait(dfd)

        assert results[0] is resp
        assert not download_func.called
