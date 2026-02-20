"""Tests for effect classification (Ticket 6A)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from roam.analysis.effects import (
    classify_symbol_effects,
    PURE, READS_DB, WRITES_DB, NETWORK, FILESYSTEM,
    TIME, RANDOM, MUTATES_GLOBAL, CACHE, QUEUE, LOGGING,
)


# ===========================================================================
# Python effect classification
# ===========================================================================


class TestPythonEffects:
    """Test Python framework-aware pattern detection."""

    def test_reads_db_django_objects(self):
        body = "users = User.objects.filter(active=True)"
        effects = classify_symbol_effects(body, "python")
        assert READS_DB in effects

    def test_reads_db_fetchall(self):
        body = "rows = cursor.fetchall()"
        effects = classify_symbol_effects(body, "python")
        assert READS_DB in effects

    def test_writes_db_save(self):
        body = "user.save()"
        effects = classify_symbol_effects(body, "python")
        assert WRITES_DB in effects

    def test_writes_db_execute(self):
        body = 'conn.execute("INSERT INTO users VALUES (?)", (name,))'
        effects = classify_symbol_effects(body, "python")
        assert WRITES_DB in effects

    def test_writes_db_commit(self):
        body = "conn.commit()"
        effects = classify_symbol_effects(body, "python")
        assert WRITES_DB in effects

    def test_network_requests(self):
        body = 'response = requests.get("https://api.example.com")'
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects

    def test_network_httpx(self):
        body = "resp = httpx.post(url, json=data)"
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects

    def test_network_urllib(self):
        body = "urllib.request.urlopen(url)"
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects

    def test_filesystem_open(self):
        body = 'with open("file.txt") as f:\n    data = f.read()'
        effects = classify_symbol_effects(body, "python")
        assert FILESYSTEM in effects

    def test_filesystem_path(self):
        body = 'p = Path("/tmp/output")\np.write_text("hello")'
        effects = classify_symbol_effects(body, "python")
        assert FILESYSTEM in effects

    def test_filesystem_os(self):
        body = "os.remove('/tmp/file')"
        effects = classify_symbol_effects(body, "python")
        assert FILESYSTEM in effects

    def test_time_sleep(self):
        body = "time.sleep(5)"
        effects = classify_symbol_effects(body, "python")
        assert TIME in effects

    def test_time_datetime(self):
        body = "now = datetime.now()"
        effects = classify_symbol_effects(body, "python")
        assert TIME in effects

    def test_random(self):
        body = "value = random.randint(1, 100)"
        effects = classify_symbol_effects(body, "python")
        assert RANDOM in effects

    def test_global_mutation(self):
        body = "global counter\ncounter += 1"
        effects = classify_symbol_effects(body, "python")
        assert MUTATES_GLOBAL in effects

    def test_cache_lru(self):
        body = "@lru_cache(maxsize=128)\ndef expensive(): pass"
        effects = classify_symbol_effects(body, "python")
        assert CACHE in effects

    def test_queue_celery(self):
        body = "task.delay(arg1, arg2)"
        effects = classify_symbol_effects(body, "python")
        assert QUEUE in effects

    def test_logging_print(self):
        body = 'print("debug info")'
        effects = classify_symbol_effects(body, "python")
        assert LOGGING in effects

    def test_logging_logger(self):
        body = 'logger.info("Processing request")'
        effects = classify_symbol_effects(body, "python")
        assert LOGGING in effects

    def test_pure_function(self):
        body = "return x + y"
        effects = classify_symbol_effects(body, "python")
        assert len(effects) == 0

    def test_pure_arithmetic(self):
        body = "result = a * b + c\nreturn result"
        effects = classify_symbol_effects(body, "python")
        assert len(effects) == 0

    def test_multiple_effects(self):
        body = (
            'data = requests.get(url).json()\n'
            'conn.execute("INSERT INTO cache VALUES (?)", (data,))\n'
            'logger.info("cached")\n'
        )
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects
        assert WRITES_DB in effects
        assert LOGGING in effects


# ===========================================================================
# JavaScript effect classification
# ===========================================================================


class TestJavaScriptEffects:
    """Test JavaScript/TypeScript pattern detection."""

    def test_network_fetch(self):
        body = 'const res = await fetch("/api/data")'
        effects = classify_symbol_effects(body, "javascript")
        assert NETWORK in effects

    def test_network_axios(self):
        body = "const res = axios.get(url)"
        effects = classify_symbol_effects(body, "javascript")
        assert NETWORK in effects

    def test_db_write_create(self):
        body = "await User.create({ name, email })"
        effects = classify_symbol_effects(body, "javascript")
        assert WRITES_DB in effects

    def test_db_read_find(self):
        body = "const user = await User.findOne({ id })"
        effects = classify_symbol_effects(body, "javascript")
        assert READS_DB in effects

    def test_fs_read(self):
        body = 'const data = fs.readFileSync("config.json")'
        effects = classify_symbol_effects(body, "javascript")
        assert FILESYSTEM in effects

    def test_time_settimeout(self):
        body = "setTimeout(() => callback(), 1000)"
        effects = classify_symbol_effects(body, "javascript")
        assert TIME in effects

    def test_random(self):
        body = "const id = Math.random()"
        effects = classify_symbol_effects(body, "javascript")
        assert RANDOM in effects

    def test_console_log(self):
        body = 'console.log("debug")'
        effects = classify_symbol_effects(body, "javascript")
        assert LOGGING in effects

    def test_typescript_same_as_js(self):
        """TypeScript uses same patterns as JavaScript."""
        body = 'const res = await fetch("/api")'
        effects = classify_symbol_effects(body, "typescript")
        assert NETWORK in effects

    def test_pure_js(self):
        body = "return arr.map(x => x * 2).filter(x => x > 5)"
        effects = classify_symbol_effects(body, "javascript")
        assert len(effects) == 0


# ===========================================================================
# PHP effect classification
# ===========================================================================


class TestPHPEffects:
    """Test PHP pattern detection."""

    def test_db_write_save(self):
        body = "$user->save();"
        effects = classify_symbol_effects(body, "php")
        assert WRITES_DB in effects

    def test_db_write_create(self):
        body = "$user = User::create(['name' => 'John']);"
        effects = classify_symbol_effects(body, "php")
        assert WRITES_DB in effects

    def test_db_read_get(self):
        body = "$users = User::where('active', true)->get();"
        effects = classify_symbol_effects(body, "php")
        assert READS_DB in effects

    def test_network_curl(self):
        body = "$ch = curl_init($url);"
        effects = classify_symbol_effects(body, "php")
        assert NETWORK in effects

    def test_filesystem_fopen(self):
        body = '$f = fopen("data.csv", "r");'
        effects = classify_symbol_effects(body, "php")
        assert FILESYSTEM in effects

    def test_cache_laravel(self):
        body = 'Cache::put("key", $value, 3600);'
        effects = classify_symbol_effects(body, "php")
        assert CACHE in effects

    def test_queue_dispatch(self):
        body = "dispatch(new ProcessPodcast($podcast));"
        effects = classify_symbol_effects(body, "php")
        assert QUEUE in effects

    def test_pure_php(self):
        body = "return $a + $b;"
        effects = classify_symbol_effects(body, "php")
        assert len(effects) == 0


# ===========================================================================
# Go effect classification
# ===========================================================================


class TestGoEffects:
    """Test Go pattern detection."""

    def test_db_read_query(self):
        body = 'rows, err := db.Query("SELECT * FROM users")'
        effects = classify_symbol_effects(body, "go")
        assert READS_DB in effects

    def test_db_write_exec(self):
        body = 'db.Exec("INSERT INTO users VALUES (?)", name)'
        effects = classify_symbol_effects(body, "go")
        assert WRITES_DB in effects

    def test_network_http(self):
        body = 'resp, err := http.Get("https://api.example.com")'
        effects = classify_symbol_effects(body, "go")
        assert NETWORK in effects

    def test_filesystem(self):
        body = 'data, err := os.ReadFile("config.json")'
        effects = classify_symbol_effects(body, "go")
        assert FILESYSTEM in effects

    def test_logging(self):
        body = 'log.Printf("Processing %d items", count)'
        effects = classify_symbol_effects(body, "go")
        assert LOGGING in effects


# ===========================================================================
# Unsupported language
# ===========================================================================


class TestUnsupportedLanguage:
    """Test behavior with unsupported languages."""

    def test_unknown_language_returns_empty(self):
        body = "some code"
        effects = classify_symbol_effects(body, "cobol")
        assert effects == set()

    def test_none_body_returns_empty(self):
        effects = classify_symbol_effects("", "python")
        assert effects == set()


# ===========================================================================
# Edge cases
# ===========================================================================


class TestEdgeCases:
    """Test edge cases and corner scenarios."""

    def test_case_insensitive_matching(self):
        """Patterns should match case-insensitively."""
        body = "REQUESTS.GET(url)"
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects

    def test_multiline_body(self):
        """Effects spread across multiple lines should be detected."""
        body = (
            "def process():\n"
            "    data = requests.get(url)\n"
            "    conn.execute('INSERT ...')\n"
            "    return data\n"
        )
        effects = classify_symbol_effects(body, "python")
        assert NETWORK in effects
        assert WRITES_DB in effects

    def test_no_false_positive_variable_names(self):
        """Variable names like 'filter_value' shouldn't trigger."""
        # This tests that patterns require parens/dots context
        body = "x = filter_value + 1"
        effects = classify_symbol_effects(body, "python")
        # .filter( requires a dot prefix and paren, so plain 'filter_value'
        # should not match
        assert READS_DB not in effects
