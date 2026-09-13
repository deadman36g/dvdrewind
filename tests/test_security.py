import os
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import aiohttp_jinja2
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from src.web.app import create_app, render_embed_fallback, render_imdb_dossier
from src.web.security import (
    ResponseTooLarge,
    URLSecurityError,
    fetch_with_aiohttp_session,
    validate_public_http_url,
    validate_url_structure,
)


class TestURLSecurity(unittest.TestCase):
    def test_blocks_local_private_reserved_and_non_http_targets(self):
        blocked = [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.1.2.3/",
            "http://192.168.50.1/",
            "http://[::1]/",
            "http://[fc00::1]/",
            "file:///etc/passwd",
            "ftp://example.com/file",
            "http://printer.local/",
            "http://router.lan/",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(URLSecurityError):
                    validate_url_structure(url)

    def test_rejects_userinfo_and_domain_suffix_tricks(self):
        with self.assertRaises(URLSecurityError):
            validate_url_structure(
                "https://imdb.com@127.0.0.1/",
                ("imdb.com",),
            )
        with self.assertRaises(URLSecurityError):
            validate_url_structure(
                "https://imdb.com.evil.com/title/tt0000001/",
                ("imdb.com",),
            )
        with self.assertRaises(URLSecurityError):
            validate_url_structure(
                "https://allowed-domain.example.evil.com/",
                ("allowed-domain.example",),
            )

    def test_allows_exact_domain_and_real_subdomain_boundary(self):
        validate_url_structure("https://imdb.com/title/tt0000001/", ("imdb.com",))
        validate_url_structure("https://www.imdb.com/title/tt0000001/", ("imdb.com",))

    def test_dns_resolution_to_private_address_is_rejected(self):
        private_answer = [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("127.0.0.1", 443)),
        ]
        with patch("socket.getaddrinfo", return_value=private_answer):
            with self.assertRaises(URLSecurityError):
                validate_public_http_url("https://www.imdb.com/title/tt0000001/", ("imdb.com",))


class _FakeContent:
    def __init__(self, chunks):
        self._chunks = chunks

    async def iter_chunked(self, _size):
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    def __init__(self, status, headers=None, chunks=None, charset="utf-8"):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(chunks or [])
        self.charset = charset

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses
        self.requested = []

    def get(self, url, allow_redirects=False):
        self.requested.append((url, allow_redirects))
        return self.responses[url]


async def _structure_only_validator(url, allowed_domains):
    return validate_url_structure(url, allowed_domains)


class TestBoundedFetch(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_from_allowed_domain_to_private_ip_is_blocked(self):
        start = "https://www.imdb.com/title/tt0000001/"
        session = _FakeSession(
            {
                start: _FakeResponse(
                    302,
                    headers={"Location": "http://127.0.0.1/admin"},
                )
            }
        )
        with self.assertRaises(URLSecurityError):
            await fetch_with_aiohttp_session(
                session,
                start,
                allowed_domains=("imdb.com",),
                validator=_structure_only_validator,
            )
        self.assertEqual([item[0] for item in session.requested], [start])

    async def test_oversized_response_is_rejected(self):
        url = "https://www.imdb.com/title/tt0000001/"
        session = _FakeSession(
            {
                url: _FakeResponse(
                    200,
                    headers={"Content-Length": "1001", "Content-Type": "text/html"},
                    chunks=[b"ok"],
                )
            }
        )
        with self.assertRaises(ResponseTooLarge):
            await fetch_with_aiohttp_session(
                session,
                url,
                allowed_domains=("imdb.com",),
                max_bytes=1000,
                validator=_structure_only_validator,
            )


class TestGeneratedHTMLEscaping(unittest.TestCase):
    def test_fallback_escapes_external_url(self):
        response = render_embed_fallback(
            'https://www.imdb.com/?q="><script>alert(1)</script>',
            "imdb",
            "test",
        )
        text = response.text
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)
        self.assertIn("&quot;&gt;", text)

    def test_imdb_dossier_extracts_relevant_json_ld_metadata(self):
        imdb_html = """
        <html><head><script type="application/ld+json">
        {
          "@context": "https://schema.org",
          "@type": "Movie",
          "name": "Blade Runner",
          "datePublished": "1982-06-25",
          "duration": "PT1H57M",
          "genre": ["Science Fiction", "Thriller"],
          "contentRating": "R",
          "description": "A blade runner must pursue and terminate four replicants.",
          "aggregateRating": {"ratingValue": 8.1, "ratingCount": 845000},
          "director": [{"@type": "Person", "name": "Ridley Scott"}],
          "actor": [
            {"@type": "Person", "name": "Harrison Ford"},
            {"@type": "Person", "name": "Rutger Hauer"}
          ]
        }
        </script></head><body></body></html>
        """

        class FakeRepo:
            def get_title_detail(self, _fid):
                return {"clean_title": "Blade Runner", "poster_url": ""}

            def close(self):
                pass

        with patch("src.web.app.ArchiveRepository", return_value=FakeRepo()):
            response = render_imdb_dossier(
                "https://www.imdb.com/title/tt0083658/",
                "43651",
                html_text=imdb_html,
            )
        text = response.text
        self.assertIn("Blade Runner", text)
        self.assertIn("8.1", text)
        self.assertIn("845000 votes", text)
        self.assertIn("1h 57m", text)
        self.assertIn("Ridley Scott", text)
        self.assertIn("Harrison Ford, Rutger Hauer", text)
        self.assertIn("Fixed scraped info view", text)

    def test_imdb_dossier_escapes_interpolated_movie_fields(self):
        class FakeRepo:
            def get_title_detail(self, _fid):
                return {
                    "clean_title": "<script>title()</script>",
                    "year": '"><svg onload=alert(1)>',
                    "runtime": "95<script>",
                    "director": "<b>Director</b>",
                    "synopsis": "<img src=x onerror=alert(1)>",
                    "genres": ["Horror", "<svg/onload=alert(1)>"] ,
                    "poster_url": 'https://example.com/poster.jpg" onerror="alert(1)',
                }

            def close(self):
                pass

        with patch("src.web.app.ArchiveRepository", return_value=FakeRepo()):
            response = render_imdb_dossier(
                'https://www.imdb.com/title/tt0000001/?x="><script>url()</script>',
                "123",
            )
        text = response.text
        self.assertNotIn("<script>title()</script>", text)
        self.assertNotIn("<img src=x onerror=alert(1)>", text)
        self.assertNotIn('onerror="alert(1)"', text)
        self.assertIn("&lt;script&gt;title()&lt;/script&gt;", text)
        self.assertIn("&lt;b&gt;Director&lt;/b&gt;", text)


class TestSecurityHTTP(AioHTTPTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from src.cli import cmd_import_fixtures

        class DummyArgs:
            pass

        try:
            cmd_import_fixtures(DummyArgs())
        except Exception:
            pass

    async def get_application(self):
        return create_app()

    @unittest_run_loop
    async def test_embed_proxy_rejects_ssrf_payloads(self):
        blocked_urls = [
            "http://127.0.0.1/",
            "http://localhost/",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.7/",
            "http://192.168.50.1/",
            "http://[::1]/",
            "http://[fc00::1]/",
            "file:///etc/passwd",
            "ftp://www.imdb.com/file",
            "https://imdb.com@127.0.0.1/",
            "https://imdb.com.evil.com/",
        ]
        for target in blocked_urls:
            with self.subTest(target=target):
                resp = await self.client.request(
                    "GET",
                    "/embed/proxy",
                    params={"service": "imdb", "url": target},
                )
                self.assertEqual(resp.status, 400)

    @unittest_run_loop
    async def test_custom_poster_url_cannot_target_private_network(self):
        resp = await self.client.request(
            "POST",
            "/api/poster/43651",
            json={"poster_url": "http://127.0.0.1/private.jpg"},
        )
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertIn("blocked", data["error"].lower())

    @unittest_run_loop
    async def test_archive_controls_reject_nonlocal_requests(self):
        for path in (
            "/api/archive/sync",
            "/api/archive/posters",
            "/api/archive/vacuum",
            "/api/archive/cancel",
        ):
            with self.subTest(path=path):
                resp = await self.client.request(
                    "POST",
                    path,
                    headers={
                        "Host": "dvdrewind.example",
                        "X-Forwarded-For": "203.0.113.10",
                    },
                    json={},
                )
                self.assertEqual(resp.status, 403)

    @unittest_run_loop
    async def test_archive_control_admin_token_allows_remote_request(self):
        with patch.dict(os.environ, {"DVDREWIND_ADMIN_TOKEN": "test-secret"}):
            resp = await self.client.request(
                "POST",
                "/api/archive/cancel",
                headers={
                    "Host": "dvdrewind.example",
                    "X-Forwarded-For": "203.0.113.10",
                    "X-DVDRewind-Admin-Token": "test-secret",
                },
            )
        self.assertEqual(resp.status, 200)

    @unittest_run_loop
    async def test_jinja_templates_are_autoescaped(self):
        env = aiohttp_jinja2.get_env(self.app)
        self.assertTrue(env.autoescape("movie.html"))

    def test_embed_iframe_does_not_receive_same_origin_privilege(self):
        template = (PROJECT_ROOT / "src" / "web" / "templates" / "movie.html").read_text(encoding="utf-8")
        iframe_line = next(line for line in template.splitlines() if 'id="inline-embed-frame"' in line)
        self.assertIn('sandbox="allow-scripts allow-forms allow-popups"', iframe_line)
        self.assertNotIn("allow-same-origin", iframe_line)

    def test_external_resource_panel_has_one_imdb_control_and_fixed_height(self):
        template = (PROJECT_ROOT / "src" / "web" / "templates" / "movie.html").read_text(encoding="utf-8")
        css = (PROJECT_ROOT / "src" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        self.assertEqual(template.count('id="btn-imdb-toggle"'), 1)
        self.assertNotIn('id="btn-imdb-link"', template)
        self.assertIn('id="btn-letterboxd-toggle"', template)
        self.assertIn('/embed/proxy?service=imdb', template)
        self.assertIn('/embed/proxy?service=letterboxd', template)
        self.assertIn('Fixed Scraped Info View', template)
        self.assertIn('height: 430px;', css)
        self.assertIn('min-height: 430px;', css)
        self.assertIn('max-height: 430px;', css)


if __name__ == "__main__":
    unittest.main()
