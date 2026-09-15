"""Telegram entity rendering and Unicode-safe splitting; no HTTP requests."""
import unittest

from test_messaging import app


def utf16(text):
    return len(text.encode("utf-16-le")) // 2


def entity_text(part, entity):
    encoded = part["text"].encode("utf-16-le")
    return encoded[entity["offset"] * 2:(entity["offset"] + entity["length"]) * 2].decode("utf-16-le")


class TelegramFormattingTests(unittest.TestCase):
    def test_headings_emphasis_lists_code_and_links(self):
        source = ('## Result\n\n**Done** with *care* and ~~old~~ text.\n\n'
                  '- Read `config.json`\n- Open [docs](https://example.com?a=1&b=2)\n\n'
                  '```python\nprint("<hello> & **literal**")\n```')
        parts = app.telegram_reply_parts(source)
        self.assertEqual(len(parts), 1)
        part = parts[0]
        self.assertNotIn("##", part["text"])
        self.assertNotIn("**Done**", part["text"])
        self.assertNotIn("```", part["text"])
        self.assertIn("• Read config.json", part["text"])
        types = {entity["type"] for entity in part["entities"]}
        self.assertEqual(types, {"bold", "italic", "strikethrough", "code", "pre", "text_link"})
        code = next(entity for entity in part["entities"] if entity["type"] == "pre")
        self.assertEqual(code["language"], "python")
        self.assertEqual(entity_text(part, code), 'print("<hello> & **literal**")\n')
        link = next(entity for entity in part["entities"] if entity["type"] == "text_link")
        self.assertEqual(link["url"], "https://example.com?a=1&b=2")
        self.assertEqual(entity_text(part, link), "docs")

    def test_raw_html_is_literal_and_unsafe_links_are_not_entities(self):
        part = app.telegram_reply_parts('<b>literal</b> & [bad](javascript:alert(1))')[0]
        self.assertIn("<b>literal</b>", part["text"])
        self.assertEqual(part["entities"], [])
        self.assertNotIn("parse_mode", part)

    def test_native_entities_use_utf16_offsets_after_emoji(self):
        part = app.telegram_reply_parts('😀 **bold 😀** then `a_b`')[0]
        bold = next(entity for entity in part["entities"] if entity["type"] == "bold")
        self.assertEqual(bold["offset"], 3)
        self.assertEqual(bold["length"], 7)
        self.assertEqual(entity_text(part, bold), "bold 😀")
        self.assertEqual(entity_text(part, part["entities"][-1]), "a_b")

    def test_long_code_and_bold_split_without_breaking_content_or_entities(self):
        for source, expected, style in [
            ('```sh\n' + '😀<&>\n' * 1000 + '```', '😀<&>\n' * 1000, "pre"),
            ('**' + ('😀&x ' * 1000).rstrip() + '**', ('😀&x ' * 1000).rstrip(), "bold"),
        ]:
            with self.subTest(style=style):
                parts = app.telegram_reply_parts(source, limit=100)
                self.assertGreater(len(parts), 1)
                self.assertEqual("".join(part["text"] for part in parts), expected)
                for part in parts:
                    self.assertLessEqual(utf16(part["text"]), 100)
                    self.assertTrue(part["text"])
                    for entity in part["entities"]:
                        self.assertEqual(entity["type"], style)
                        self.assertGreater(entity["length"], 0)
                        self.assertLessEqual(entity["offset"] + entity["length"], utf16(part["text"]))
                        entity_text(part, entity)  # decoding fails if a surrogate was split

    def test_nested_emphasis_is_valid_but_code_is_not_nested(self):
        part = app.telegram_reply_parts('**bold *italic* and `code` again**')[0]
        code = next(entity for entity in part["entities"] if entity["type"] == "code")
        for entity in part["entities"]:
            if entity is not code:
                self.assertTrue(entity["offset"] + entity["length"] <= code["offset"]
                                or entity["offset"] >= code["offset"] + code["length"])
        self.assertIn("italic", [entity["type"] for entity in part["entities"]])

    def test_tables_and_ordered_lists_are_readable(self):
        source = '| Name | Status |\n| --- | --- |\n| alpha | ready |\n\n3. First\n4. Second'
        part = app.telegram_reply_parts(source)[0]
        self.assertIn("Name | Status\nalpha | ready", part["text"])
        self.assertNotIn("---", part["text"])
        self.assertIn("3. First\n4. Second", part["text"])

    def test_relative_file_links_preserve_paths_without_invalid_link_entities(self):
        part = app.telegram_reply_parts('[file](src/main.py)')[0]
        self.assertEqual(part["text"], 'file (src/main.py)')
        self.assertEqual(part["entities"], [])

    def test_truncation_empty_response_and_excessive_entities(self):
        parts = app.telegram_reply_parts('x' * 13000)
        self.assertIn("Response truncated", parts[-1]["text"])
        self.assertLessEqual(sum(len(part["text"]) for part in parts), 12050)
        self.assertIn("without a text response", app.telegram_reply_parts(' \n')[0]["text"])
        part = app.telegram_reply_parts('**x** ' * 150)[0]
        self.assertEqual(part["entities"], [])
        self.assertNotIn('**', part["text"])

    def test_unclosed_fence_is_still_a_valid_code_entity(self):
        part = app.telegram_reply_parts('```bash\nprintf "hello"')[0]
        self.assertNotIn('```', part["text"])
        self.assertEqual(part["entities"][0]["type"], "pre")


if __name__ == "__main__":
    unittest.main()
