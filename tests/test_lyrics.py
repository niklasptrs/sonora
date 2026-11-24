import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lyrics import animated_line, LyricsClient, LyricsError, MAX_BYTES, load_lyrics, parse_lyrics


class Response:
    def __init__(self, value, content_type='application/json'):
        self.data = json.dumps(value).encode() if not isinstance(value, bytes) else value
        self.headers = {'Content-Type':content_type}
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def geturl(self): return 'https://lrclib.net/api/get'
    def read(self, count): return self.data[:count]


class LyricsParserTests(unittest.TestCase):
    def test_multiple_timestamps_offset_and_fraction_precision(self):
        doc = parse_lyrics('[ar:Invented artist]\n[offset: +250]\n[00:02.12][00:05.123]Imaginary sky\n[00:03.5]Paper lantern\n[00:06.000]')
        self.assertEqual([line.time_ms for line in doc.lines], [1870,3250,4873,5750])
        self.assertEqual(doc.line_at(1870).text, 'Imaginary sky')
        self.assertIsNone(doc.line_at(1869))
        self.assertEqual(doc.line_at(5750).text, '')
        self.assertNotIn('[ar:', doc.plain)

    def test_negative_offset_delays_and_last_global_offset_wins(self):
        doc = parse_lyrics('[offset:100]\n[00:01]Cardboard moon\n[offset:-200]')
        self.assertEqual(doc.lines[0].time_ms,1200)

    def test_sorting_clamping_and_enhanced_lrc(self):
        doc = parse_lyrics('[offset:500]\n[00:05]<00:05.000>Glass <00:05.250>river\n[00:00.100]Quiet start')
        self.assertEqual([line.time_ms for line in doc.lines],[0,4500])
        self.assertEqual(doc.lines[1].text, 'Glass river')
        for pos in (None, 'bad', float('nan'),float('inf'),-1):
            self.assertEqual(doc.index_at(pos),-1)

    def test_animations_follow_playback_and_word_cues(self):
        line = parse_lyrics('[00:01]<00:01>Für <00:01.45>dich').lines[0]
        self.assertEqual(animated_line(line, 1400, 'Wortaufbau', True).text.strip(), 'Für')
        self.assertEqual(animated_line(line, 1450, 'Wortaufbau', True).text, 'Für dich')
        self.assertLess(animated_line(line, 1100, 'Seitengleiter', True).dx, 0)
        self.assertNotEqual(animated_line(line, 1050, 'Federn', True).dy, 0)
        self.assertEqual(animated_line(line, 1050, 'Federn', False).dy, 0)

    def test_plain_text_and_brackets_are_preserved(self):
        doc = parse_lyrics('Invented first line\n\nSecond [echo] line')
        self.assertFalse(doc.lines)
        self.assertEqual(doc.plain,'Invented first line\n\nSecond [echo] line')

    def test_local_file_limit_and_encoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sample.lrc'
            path.write_bytes(b'\xef\xbb\xbf[00:01.0]A tiny fictional line')
            self.assertEqual(load_lyrics(path).line_at(1000).text,'A tiny fictional line')
            path.write_bytes(b'x'*(MAX_BYTES+1))
            with self.assertRaises(LyricsError): load_lyrics(path)
            path.write_bytes(b'\xff\xfe')
            with self.assertRaises(LyricsError): load_lyrics(path)
            with self.assertRaises(LyricsError): load_lyrics(Path(directory)/'absent')
        with self.assertRaises(LyricsError): parse_lyrics('x'*(MAX_BYTES+1))
        with self.assertRaises(LyricsError): parse_lyrics('binary\x00data')


class LyricsClientTests(unittest.TestCase):
    def client(self): return LyricsClient('Sonora v0.1 (https://example.org/sonora)')

    def test_explicit_client_identification_required(self):
        for value in ('', 'Python urllib', 'Sonora\r\nBad: header',None):
            with self.assertRaises(LyricsError): LyricsClient(value)

    def test_lookup_fields_encoding_and_sync_result(self):
        response = Response({'plainLyrics':'Fictional plain line','syncedLyrics':'[00:01.250]Fictional timed line','instrumental':False})
        with patch('lyrics.urlopen',return_value=response) as open_url:
            result = self.client().lookup('Paper & stars','Imaginary artist','Cloud album',123.5)
            req = open_url.call_args.args[0]
            query = parse_qs(urlparse(req.full_url).query)
            self.assertEqual(query['track_name'],['Paper & stars'])
            self.assertEqual(query['duration'],['123.5'])
            self.assertTrue(req.get_header('User-agent').startswith('Sonora'))
        self.assertEqual(result.plain,'Fictional plain line')
        self.assertEqual(result.line_at(1250).text,'Fictional timed line')
        self.assertEqual(result.source,'LRCLIB')

    def test_not_found_returns_none(self):
        error = HTTPError('https://lrclib.net/api/get',404,'Not found',{},io.BytesIO(b''))
        with patch('lyrics.urlopen',side_effect=error):
            self.assertIsNone(self.client().lookup('Paper boat','Test artist'))

    def test_instrumental_response(self):
        with patch('lyrics.urlopen',return_value=Response({'plainLyrics':None,'syncedLyrics':None,'instrumental':True})):
            result = self.client().lookup('Wordless cloud','Test artist')
        self.assertTrue(result.instrumental)
        self.assertEqual(result.plain,'')

    def test_retry_after_prevents_followup_network_request(self):
        client = self.client()
        error = HTTPError('https://lrclib.net/api/get',429,'Limit',{'Retry-After':'120'},io.BytesIO(b''))
        with patch('lyrics.urlopen',side_effect=error) as open_url:
            with self.assertRaises(LyricsError) as first:
                client.lookup('Paper boat','Test artist')
            self.assertGreaterEqual(first.exception.retry_after,120)
            with self.assertRaises(LyricsError) as second:
                client.lookup('Another paper boat','Test artist')
            self.assertGreater(second.exception.retry_after,100)
            self.assertEqual(open_url.call_count,1)

    def test_invalid_data_and_network_failures(self):
        cases = [Response(b'not json'),Response({'plainLyrics':[]}),Response(b'x'*(MAX_BYTES+1)),Response({},'text/html')]
        for response in cases:
            with self.subTest(response=response), patch('lyrics.urlopen',return_value=response):
                with self.assertRaises(LyricsError): self.client().lookup('Paper boat','Test artist')
        with patch('lyrics.urlopen',side_effect=URLError('offline')):
            with self.assertRaises(LyricsError): self.client().lookup('Paper boat','Test artist')

    def test_invalid_request_stays_offline(self):
        with patch('lyrics.urlopen') as open_url:
            for args in (('', 'Artist'), ('Title','Artist','',float('nan')),('Title','Artist','',4000)):
                with self.assertRaises(LyricsError): self.client().lookup(*args)
            open_url.assert_not_called()
