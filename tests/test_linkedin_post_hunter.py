"""
Unit Tests untuk LinkedIn Post Hunter Bot
==========================================
Menguji setiap komponen secara terisolasi menggunakan mock objects.
Tidak ada request HTTP sungguhan yang dibuat selama testing.

Jalankan: pytest tests/test_linkedin_post_hunter.py -v
"""

import os
import re
import pytest
from unittest.mock import patch, MagicMock, call

# Set environment variables SEBELUM import modul utama,
# supaya konstanta global ter-inisialisasi dengan benar.
os.environ["NOTION_TOKEN"] = "test-notion-token"
os.environ["NOTION_LINKEDIN_DB_ID"] = "test-db-id-12345"
os.environ["GOOGLE_CSE_API_KEY"] = "test-google-api-key"
os.environ["GOOGLE_CSE_ID"] = "test-cse-id"

import linkedin_post_hunter as lph


# ==========================================
# FIXTURES
# ==========================================
@pytest.fixture
def sample_cse_item():
    """Contoh item dari Google CSE API response."""
    return {
        "title": "John Doe di LinkedIn: We're hiring a Software Engineer for our remote team in Indonesia!",
        "link": "https://www.linkedin.com/posts/johndoe_activity-1234567890",
        "snippet": "Oct 15, 2026 — We're hiring a Software Engineer for our remote team in Indonesia! "
                   "Must have 3+ years of Python experience. Apply now! #hiring #remote",
    }


@pytest.fixture
def sample_cse_item_english():
    """Item CSE dengan format title bahasa Inggris."""
    return {
        "title": "Jane Smith on LinkedIn: Looking for a Backend Developer",
        "link": "https://www.linkedin.com/posts/janesmith_activity-9876543210",
        "snippet": "2 days ago — Looking for a Backend Developer with Go/Python experience. "
                   "Remote-friendly, based in Indonesia.",
    }


@pytest.fixture
def sample_cse_item_no_author():
    """Item CSE tanpa format author yang jelas."""
    return {
        "title": "Exciting opportunity - Senior Frontend Developer needed",
        "link": "https://www.linkedin.com/posts/randomuser_activity-1111111111",
        "snippet": "Senior Frontend Developer needed. React/TypeScript. Remote. Indonesia.",
    }


@pytest.fixture
def sample_cse_item_not_linkedin():
    """Item yang bukan LinkedIn post (untuk tes filtering)."""
    return {
        "title": "Some random page",
        "link": "https://www.example.com/not-linkedin",
        "snippet": "This is not a LinkedIn post.",
    }


@pytest.fixture
def sample_notion_response_page1():
    """Simulasi response Notion halaman 1 (ada halaman selanjutnya)."""
    return {
        "results": [
            {
                "properties": {
                    "URL": {"url": "https://www.linkedin.com/posts/user1_activity-111"}
                }
            },
            {
                "properties": {
                    "URL": {"url": "https://www.linkedin.com/posts/user2_activity-222"}
                }
            },
        ],
        "has_more": True,
        "next_cursor": "cursor-page-2",
    }


@pytest.fixture
def sample_notion_response_page2():
    """Simulasi response Notion halaman 2 (halaman terakhir)."""
    return {
        "results": [
            {
                "properties": {
                    "URL": {"url": "https://www.linkedin.com/posts/user3_activity-333"}
                }
            },
        ],
        "has_more": False,
        "next_cursor": None,
    }


# ==========================================
# TEST: KEYWORD MATCHING
# ==========================================
class TestKeywordHit:
    """Menguji fungsi keyword_hit() yang berbasis word-boundary regex."""

    def test_exact_match(self):
        assert lph.keyword_hit("Software Engineer needed", ["engineer"])

    def test_no_match(self):
        assert not lph.keyword_hit("Marketing Manager", ["engineer"])

    def test_word_boundary_prevents_false_positive(self):
        """'ai' tidak boleh cocok dengan 'retail' atau 'email'."""
        assert not lph.keyword_hit("Retail Manager position", ["ai"])
        assert not lph.keyword_hit("Email Marketing Specialist", ["ai"])

    def test_word_boundary_allows_standalone(self):
        """'ai' harus cocok ketika berdiri sendiri."""
        assert lph.keyword_hit("AI Engineer for NLP team", ["ai"])

    def test_qa_boundary(self):
        """'qa' tidak boleh cocok dengan 'square' tapi harus cocok sendiri."""
        assert not lph.keyword_hit("Square Enix Developer", ["qa"])
        assert lph.keyword_hit("QA Engineer needed", ["qa"])

    def test_special_char_keyword(self):
        """'.net' harus cocok tanpa masalah boundary."""
        assert lph.keyword_hit("Hiring .NET Developer", [".net"])

    def test_case_insensitive(self):
        assert lph.keyword_hit("BACKEND DEVELOPER", ["backend"])
        assert lph.keyword_hit("Backend Developer", ["backend"])

    def test_multi_word_keyword(self):
        assert lph.keyword_hit("Looking for a Data Engineer", ["data engineer"])

    def test_multiple_keywords_any_match(self):
        assert lph.keyword_hit("Frontend React Developer", ["backend", "frontend"])

    def test_empty_text(self):
        assert not lph.keyword_hit("", ["engineer"])

    def test_empty_keywords(self):
        assert not lph.keyword_hit("Software Engineer", [])


# ==========================================
# TEST: COMPILE BOUNDARY PATTERN
# ==========================================
class TestCompileBoundaryPattern:
    """Menguji fungsi _compile_boundary_pattern()."""

    def test_alphanumeric_keyword_has_boundaries(self):
        pattern = lph._compile_boundary_pattern("developer")
        assert pattern.pattern == r"\bdeveloper\b"

    def test_dot_start_keyword_no_left_boundary(self):
        pattern = lph._compile_boundary_pattern(".net")
        assert pattern.pattern == r"\.net\b"

    def test_hash_end_keyword_no_right_boundary(self):
        pattern = lph._compile_boundary_pattern("c#")
        assert pattern.pattern == r"\bc\#"

    def test_whitespace_stripped(self):
        pattern = lph._compile_boundary_pattern("  developer  ")
        assert pattern.pattern == r"\bdeveloper\b"


# ==========================================
# TEST: CSE ITEM PARSER
# ==========================================
class TestParseCseItem:
    """Menguji parse_post_from_cse_item()."""

    def test_parse_valid_item_indonesian_format(self, sample_cse_item):
        result = lph.parse_post_from_cse_item(sample_cse_item)
        assert result is not None
        assert result["author"] == "John Doe"
        assert "linkedin.com/posts" in result["link"]
        assert result["content"] != ""
        assert result["posted_date"] == "Oct 15, 2026"

    def test_parse_valid_item_english_format(self, sample_cse_item_english):
        result = lph.parse_post_from_cse_item(sample_cse_item_english)
        assert result is not None
        assert result["author"] == "Jane Smith"
        assert "janesmith" in result["link"]

    def test_parse_item_no_clear_author(self, sample_cse_item_no_author):
        result = lph.parse_post_from_cse_item(sample_cse_item_no_author)
        assert result is not None
        # Harus tetap punya author (fallback ke bagian sebelum dash)
        assert result["author"] == "Exciting opportunity"

    def test_parse_non_linkedin_item_returns_none(self, sample_cse_item_not_linkedin):
        result = lph.parse_post_from_cse_item(sample_cse_item_not_linkedin)
        assert result is None

    def test_parse_empty_link(self):
        item = {"title": "Test", "link": "", "snippet": "Test snippet"}
        assert lph.parse_post_from_cse_item(item) is None

    def test_parse_missing_fields(self):
        item = {}
        assert lph.parse_post_from_cse_item(item) is None

    def test_parse_date_extraction_relative(self):
        """Tes ekstraksi tanggal format relatif (e.g., '2 days ago')."""
        item = {
            "title": "User on LinkedIn: Hiring",
            "link": "https://www.linkedin.com/posts/test_activity-123",
            "snippet": "2 days ago — We are hiring developers.",
        }
        result = lph.parse_post_from_cse_item(item)
        assert result is not None
        assert result["posted_date"] == "2 days ago"

    def test_parse_date_extraction_iso(self):
        """Tes ekstraksi tanggal format ISO."""
        item = {
            "title": "User on LinkedIn: Hiring",
            "link": "https://www.linkedin.com/posts/test_activity-456",
            "snippet": "Posted 2026-10-15 — Looking for engineers.",
        }
        result = lph.parse_post_from_cse_item(item)
        assert result is not None
        assert result["posted_date"] == "2026-10-15"


# ==========================================
# TEST: RELEVANCE FILTERING
# ==========================================
class TestIsRelevantPost:
    """Menguji is_relevant_post()."""

    def test_relevant_post_with_hiring_keyword(self):
        post = {
            "title": "We're hiring Software Engineer",
            "content": "Looking for a developer with Python experience.",
        }
        assert lph.is_relevant_post(post)

    def test_irrelevant_post_marketing(self):
        post = {
            "title": "Hiring Marketing Manager",
            "content": "Join our marketing team as a senior manager.",
        }
        assert not lph.is_relevant_post(post)

    def test_irrelevant_post_no_keywords(self):
        post = {
            "title": "Beautiful sunset photo",
            "content": "Enjoying the weekend at the beach!",
        }
        assert not lph.is_relevant_post(post)

    def test_relevant_via_content_only(self):
        """Post yang title-nya ambigu tapi content-nya relevan."""
        post = {
            "title": "New opportunity at our company",
            "content": "We're hiring a senior backend developer for remote work.",
        }
        assert lph.is_relevant_post(post)

    def test_exclude_overrides_soft(self):
        """EXCLUDE_KEYWORDS harus menang atas SOFT_KEYWORDS."""
        post = {
            "title": "Hiring Graphic Designer and Developer",
            "content": "Need someone who can do both graphic design and coding.",
        }
        assert not lph.is_relevant_post(post)


# ==========================================
# TEST: NOTION READ (DEDUPLICATION)
# ==========================================
class TestGetExistingUrls:
    """Menguji get_existing_urls() dengan paginasi."""

    @patch("linkedin_post_hunter.safe_post")
    def test_single_page_no_more(self, mock_post):
        """Satu halaman, has_more=False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"properties": {"URL": {"url": "https://linkedin.com/posts/a"}}},
                {"properties": {"URL": {"url": "https://linkedin.com/posts/b"}}},
            ],
            "has_more": False,
            "next_cursor": None,
        }
        mock_post.return_value = mock_response

        urls = lph.get_existing_urls()
        assert len(urls) == 2
        assert "https://linkedin.com/posts/a" in urls
        assert "https://linkedin.com/posts/b" in urls

    @patch("linkedin_post_hunter.safe_post")
    def test_multi_page_pagination(
        self, mock_post, sample_notion_response_page1, sample_notion_response_page2
    ):
        """Dua halaman dengan paginasi cursor."""
        mock_resp1 = MagicMock()
        mock_resp1.status_code = 200
        mock_resp1.json.return_value = sample_notion_response_page1

        mock_resp2 = MagicMock()
        mock_resp2.status_code = 200
        mock_resp2.json.return_value = sample_notion_response_page2

        mock_post.side_effect = [mock_resp1, mock_resp2]

        urls = lph.get_existing_urls()
        assert len(urls) == 3
        assert "https://www.linkedin.com/posts/user3_activity-333" in urls

    @patch("linkedin_post_hunter.safe_post")
    def test_api_error_returns_partial(self, mock_post):
        """Jika API error, kembalikan data yang sudah terbaca."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        mock_post.return_value = mock_response

        urls = lph.get_existing_urls()
        assert isinstance(urls, set)
        assert len(urls) == 0

    @patch("linkedin_post_hunter.safe_post")
    def test_connection_error_returns_empty(self, mock_post):
        """Jika koneksi gagal total, kembalikan set kosong."""
        mock_post.side_effect = Exception("Connection refused")

        urls = lph.get_existing_urls()
        assert isinstance(urls, set)
        assert len(urls) == 0

    @patch("linkedin_post_hunter.safe_post")
    def test_missing_url_property_skipped(self, mock_post):
        """Entry tanpa property URL harus di-skip tanpa error."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {"properties": {"URL": {"url": None}}},
                {"properties": {"URL": {"url": "https://linkedin.com/posts/valid"}}},
                {"properties": {}},
            ],
            "has_more": False,
            "next_cursor": None,
        }
        mock_post.return_value = mock_response

        urls = lph.get_existing_urls()
        assert len(urls) == 1
        assert "https://linkedin.com/posts/valid" in urls


# ==========================================
# TEST: NOTION WRITE
# ==========================================
class TestSendToNotion:
    """Menguji send_to_notion()."""

    @patch("linkedin_post_hunter.safe_post")
    def test_successful_send(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = lph.send_to_notion(
            post_title="Test Post",
            post_url="https://linkedin.com/posts/test",
            author="Test Author",
            content="Test content body",
            posted_date="Oct 15, 2026",
            search_query='"hiring" "developer"',
        )
        assert result is True
        mock_post.assert_called_once()

        # Verifikasi payload yang dikirim
        sent_data = mock_post.call_args[1]["json"]
        assert sent_data["parent"]["database_id"] == "test-db-id-12345"
        assert sent_data["properties"]["Post Title"]["title"][0]["text"]["content"] == "Test Post"
        assert sent_data["properties"]["URL"]["url"] == "https://linkedin.com/posts/test"
        assert sent_data["properties"]["Author"]["rich_text"][0]["text"]["content"] == "Test Author"

    @patch("linkedin_post_hunter.safe_post")
    def test_failed_send_returns_false(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_post.return_value = mock_response

        result = lph.send_to_notion(
            post_title="Test",
            post_url="https://linkedin.com/posts/fail",
            author="Author",
            content="Content",
            posted_date="",
            search_query="query",
        )
        assert result is False

    @patch("linkedin_post_hunter.safe_post")
    def test_connection_error_returns_false(self, mock_post):
        mock_post.side_effect = Exception("Timeout")

        result = lph.send_to_notion(
            post_title="Test",
            post_url="https://linkedin.com/posts/error",
            author="Author",
            content="Content",
            posted_date="",
            search_query="query",
        )
        assert result is False

    @patch("linkedin_post_hunter.safe_post")
    def test_content_truncation(self, mock_post):
        """Content lebih dari 2000 karakter harus dipotong."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        long_content = "x" * 3000
        lph.send_to_notion(
            post_title="Test",
            post_url="https://linkedin.com/posts/trunc",
            author="Author",
            content=long_content,
            posted_date="",
            search_query="query",
        )

        sent_data = mock_post.call_args[1]["json"]
        actual_content = sent_data["properties"]["Content"]["rich_text"][0]["text"]["content"]
        assert len(actual_content) == 2000


# ==========================================
# TEST: GOOGLE CSE SEARCH
# ==========================================
class TestSearchLinkedinPosts:
    """Menguji search_linkedin_posts()."""

    @patch("linkedin_post_hunter.safe_get")
    def test_successful_search(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "items": [
                {"title": "Post 1", "link": "https://linkedin.com/posts/1", "snippet": "..."},
                {"title": "Post 2", "link": "https://linkedin.com/posts/2", "snippet": "..."},
            ]
        }
        mock_get.return_value = mock_response

        items = lph.search_linkedin_posts('"hiring" "developer"')
        assert len(items) == 2

        # Verifikasi parameter query mengandung site: restriction
        called_params = mock_get.call_args[1]["params"]
        assert "site:linkedin.com/posts" in called_params["q"]
        assert called_params["dateRestrict"] == "d1"

    @patch("linkedin_post_hunter.safe_get")
    def test_empty_results(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"items": []}
        mock_get.return_value = mock_response

        items = lph.search_linkedin_posts('"no results"')
        assert len(items) == 0

    @patch("linkedin_post_hunter.safe_get")
    def test_no_items_key(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"searchInformation": {"totalResults": "0"}}
        mock_get.return_value = mock_response

        items = lph.search_linkedin_posts('"empty"')
        assert len(items) == 0

    @patch("linkedin_post_hunter.safe_get")
    def test_api_error_returns_empty(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_get.return_value = mock_response

        items = lph.search_linkedin_posts('"error"')
        assert len(items) == 0

    @patch("linkedin_post_hunter.safe_get")
    def test_connection_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("Network error")

        items = lph.search_linkedin_posts('"network fail"')
        assert len(items) == 0

    @patch("linkedin_post_hunter.safe_get")
    def test_invalid_json_returns_empty(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.side_effect = ValueError("Not JSON")
        mock_get.return_value = mock_response

        items = lph.search_linkedin_posts('"bad json"')
        assert len(items) == 0


# ==========================================
# TEST: ENGINE UTAMA (INTEGRATION-LIKE)
# ==========================================
class TestFetchLinkedinPosts:
    """Menguji fetch_linkedin_posts() — integrasi ringan dengan mock."""

    @patch("linkedin_post_hunter.time.sleep")
    @patch("linkedin_post_hunter.send_to_notion")
    @patch("linkedin_post_hunter.search_linkedin_posts")
    def test_end_to_end_flow(self, mock_search, mock_send, mock_sleep):
        """Tes flow lengkap: search → parse → filter → send."""
        mock_search.return_value = [
            {
                "title": "Alice on LinkedIn: Hiring Software Developer for Indonesia remote team",
                "link": "https://www.linkedin.com/posts/alice_activity-001",
                "snippet": "Oct 17, 2026 — Hiring Software Developer. Python/Django. Remote. Indonesia.",
            },
            {
                "title": "Bob on LinkedIn: Need a Marketing Manager",
                "link": "https://www.linkedin.com/posts/bob_activity-002",
                "snippet": "Marketing Manager needed for our growing sales team.",
            },
        ]
        mock_send.return_value = True
        existing_urls = set()

        count = lph.fetch_linkedin_posts(existing_urls)

        # Hanya post Alice yang relevan (Bob = marketing = blacklisted)
        assert count >= 1
        # Minimal satu call ke send_to_notion
        assert mock_send.called

    @patch("linkedin_post_hunter.time.sleep")
    @patch("linkedin_post_hunter.send_to_notion")
    @patch("linkedin_post_hunter.search_linkedin_posts")
    def test_duplicate_skipped(self, mock_search, mock_send, mock_sleep):
        """Post yang sudah ada di existing_urls harus di-skip."""
        mock_search.return_value = [
            {
                "title": "Carol on LinkedIn: Hiring Backend Developer",
                "link": "https://www.linkedin.com/posts/carol_activity-003",
                "snippet": "Backend Developer for Indonesia team.",
            },
        ]
        mock_send.return_value = True
        existing_urls = {"https://www.linkedin.com/posts/carol_activity-003"}

        count = lph.fetch_linkedin_posts(existing_urls)

        assert count == 0
        mock_send.assert_not_called()

    @patch("linkedin_post_hunter.time.sleep")
    @patch("linkedin_post_hunter.send_to_notion")
    @patch("linkedin_post_hunter.search_linkedin_posts")
    def test_send_failure_not_counted(self, mock_search, mock_send, mock_sleep):
        """Jika send_to_notion gagal, post tidak dihitung sebagai berhasil."""
        mock_search.return_value = [
            {
                "title": "Dave on LinkedIn: Hiring Frontend Developer",
                "link": "https://www.linkedin.com/posts/dave_activity-004",
                "snippet": "Frontend Developer with React experience needed.",
            },
        ]
        mock_send.return_value = False  # Gagal kirim
        existing_urls = set()

        count = lph.fetch_linkedin_posts(existing_urls)
        assert count == 0


# ==========================================
# TEST: GRACEFUL DEGRADATION
# ==========================================
class TestRunEngine:
    """Menguji run_engine() - isolasi engine dari crash."""

    def test_successful_engine(self):
        mock_func = MagicMock(return_value=5)
        existing = set()

        count = lph.run_engine("TestEngine", mock_func, existing)
        assert count == 5
        mock_func.assert_called_once_with(existing)

    def test_engine_crash_returns_zero(self):
        """Engine yang crash harus return 0, bukan propagate exception."""
        def crashing_engine(urls):
            raise RuntimeError("Unexpected crash!")

        count = lph.run_engine("CrashEngine", crashing_engine, set())
        assert count == 0


# ==========================================
# TEST: VALIDATE CONFIG
# ==========================================
class TestValidateConfig:
    """Menguji validate_config()."""

    def test_all_vars_present(self):
        """Semua env vars sudah di-set → True."""
        assert lph.validate_config() is True

    @patch.object(lph, "NOTION_TOKEN", None)
    def test_missing_notion_token(self):
        assert lph.validate_config() is False

    @patch.object(lph, "GOOGLE_CSE_API_KEY", None)
    def test_missing_google_key(self):
        assert lph.validate_config() is False

    @patch.object(lph, "NOTION_LINKEDIN_DB_ID", None)
    def test_missing_db_id(self):
        assert lph.validate_config() is False


# ==========================================
# TEST: RETRY / SAFE REQUEST HELPERS
# ==========================================
class TestSafeGet:
    """Menguji safe_get() dengan retry logic."""

    @patch("linkedin_post_hunter.requests.get")
    def test_successful_request(self, mock_get):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        result = lph.safe_get("https://example.com")
        assert result.status_code == 200

    @patch("linkedin_post_hunter.requests.get")
    def test_retryable_status_raises(self, mock_get):
        """Status 429/500/502/503/504 harus memicu RetryableHTTPError."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_get.return_value = mock_response

        with pytest.raises(lph.RetryableHTTPError):
            # Akan retry 5x lalu raise
            lph.safe_get("https://example.com/rate-limited")


class TestSafePost:
    """Menguji safe_post() dengan retry logic."""

    @patch("linkedin_post_hunter.requests.post")
    def test_successful_post(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_post.return_value = mock_response

        result = lph.safe_post("https://api.notion.com/test", json={"key": "value"})
        assert result.status_code == 200


# ==========================================
# TEST: RETRYABLE HTTP ERROR
# ==========================================
class TestRetryableHTTPError:
    def test_error_message(self):
        err = lph.RetryableHTTPError(429, "https://example.com")
        assert "429" in str(err)
        assert "example.com" in str(err)
        assert err.status_code == 429

    def test_error_inherits_exception(self):
        err = lph.RetryableHTTPError(500, "https://api.test.com")
        assert isinstance(err, Exception)


# ==========================================
# TEST: MAIN EXECUTION
# ==========================================
class TestMainExecution:
    """Menguji main_execution() flow."""

    @patch("linkedin_post_hunter.run_engine")
    @patch("linkedin_post_hunter.get_existing_urls")
    @patch("linkedin_post_hunter.validate_config")
    def test_normal_flow(self, mock_validate, mock_get_urls, mock_run):
        mock_validate.return_value = True
        mock_get_urls.return_value = set()
        mock_run.return_value = 3

        lph.main_execution()

        mock_validate.assert_called_once()
        mock_get_urls.assert_called_once()
        mock_run.assert_called_once()

    @patch("linkedin_post_hunter.run_engine")
    @patch("linkedin_post_hunter.get_existing_urls")
    @patch("linkedin_post_hunter.validate_config")
    def test_invalid_config_aborts(self, mock_validate, mock_get_urls, mock_run):
        """Jika config tidak valid, eksekusi harus dibatalkan."""
        mock_validate.return_value = False

        lph.main_execution()

        mock_get_urls.assert_not_called()
        mock_run.assert_not_called()


# ==========================================
# TEST: CLEAN DATABASE ID
# ==========================================
class TestCleanDatabaseId:
    def test_clean_raw_id(self):
        raw = "206019eb96ea4e41ba8df0d00f684cf0"
        assert lph.clean_database_id(raw) == "206019eb96ea4e41ba8df0d00f684cf0"

    def test_clean_hyphenated_uuid(self):
        raw = "206019eb-96ea-4e41-ba8d-f0d00f684cf0"
        assert lph.clean_database_id(raw) == "206019eb96ea4e41ba8df0d00f684cf0"

    def test_clean_full_notion_url(self):
        url = "https://www.notion.so/myworkspace/206019eb96ea4e41ba8df0d00f684cf0?v=abcdef"
        assert lph.clean_database_id(url) == "206019eb96ea4e41ba8df0d00f684cf0"

    def test_clean_empty_and_none(self):
        assert lph.clean_database_id(None) == ""
        assert lph.clean_database_id("") == ""
