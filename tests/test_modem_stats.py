import base64
import itertools
import json
import math
import os.path
import urllib.parse

import pytest
import requests

import cable_modem_stats


class TestOutput:

    downstream = (
        cable_modem_stats.DownstreamChannel(1, 783.0, 8.1, 39.6, 0, 0),
        cable_modem_stats.DownstreamChannel(2, 789.0, 9.6, 40.5, 535, 469),
        cable_modem_stats.DownstreamChannel(5, 807.0, 8.7, 39.2, 0, 0),
        cable_modem_stats.DownstreamChannel(8, 825.0, 8.6, 39.3, 0, 896),
    )
    upstream = (
        cable_modem_stats.UpstreamChannel(1, 19.6, 36.2, 40.0),
        cable_modem_stats.UpstreamChannel(3, 26.0, 37.6, 40.0),
    )

    def test_output_json(self):
        modem = cable_modem_stats.CableModem(modem_url='http://example.com')
        modem._record_when()
        modem.downstream_channels = self.downstream
        modem.upstream_channels = self.upstream
        output_json = modem.format_modem_data('json')
        assert output_json is not None
        output = json.loads(output_json)
        assert 'downstream' in output
        assert len(output['downstream']) == 4
        assert 'upstream' in output
        assert len(output['upstream']) == 2
        for actual, expected in itertools.zip_longest(output['downstream'], self.downstream):
            assert actual[0] == expected.channel_id
            assert actual[1] == expected.frequency
            assert actual[2] == expected.power
            assert actual[3] == expected.snr
            assert actual[4] == expected.corrected
            assert actual[5] == expected.uncorrectables
        for actual, expected in itertools.zip_longest(output['upstream'], self.upstream):
            assert actual[0] == expected.channel_id
            assert actual[1] == expected.frequency
            assert actual[2] == expected.power
            assert actual[3] == expected.snr

    def test_output_influx(self):
        modem = cable_modem_stats.CableModem(modem_url='http://example.com')
        modem._record_when()
        modem.downstream_channels = self.downstream
        modem.upstream_channels = self.upstream
        output_influx = modem.format_modem_data('influx')
        assert output_influx is not None
        output = output_influx.split('\n')
        assert len(output) == 6
        channel_down = output[0].split()
        measurement, tags = channel_down[0].split(',', 1)
        assert measurement == 'cable_modem'
        # This can be in any order before Python 3.7.
        tags_dict = dict(element.split('=', 1) for element in tags.split(','))
        assert tags_dict == {'channel': '1', 'direction': 'downstream'}
        for actual, expected in itertools.zip_longest(output[:4], self.downstream):
            _tags, fields, _time = actual.split()
            expected_fields = ','.join('%s=%s' % (field, getattr(expected, field))
                                       for field in expected._fields)
            assert fields == expected_fields

        channel_up = output[5].split()
        measurement, tags = channel_up[0].split(',', 1)
        assert measurement == 'cable_modem'
        tags_dict = dict(element.split('=', 1) for element in tags.split(','))
        assert tags_dict == {'direction': 'upstream', 'channel': '2'}
        for actual, expected in itertools.zip_longest(output[4:], self.upstream):
            _tags, fields, _time = actual.split()
            expected_fields = ','.join('%s=%s' % (field, getattr(expected, field))
                                       for field in expected._fields)
            assert fields == expected_fields


def test_modem(requests_mock):
    login_path = os.path.join(os.path.dirname(__file__), 'modems',
                              'Motorola', 'MB7621-login.html')
    stats_path = os.path.join(os.path.dirname(__file__), 'modems',
                              'Motorola', 'MB7621-MotoConnection.html')
    with open(login_path) as login_fd:
        login_page = login_fd.read()
    with open(stats_path) as stats_fd:
        stats_page = stats_fd.read()

    page_status = {
        'auth': ('username', 'pass1234'),
        'logged_in': False,
    }
    def mb7621_request(request, context):
        """An initial requests to the MotoConnection.html page will return the
        login page if nothing has authenticated recently. Return the login page
        until authenticated for the tests.
        """
        # Modem returns 200 
        context.status_code = 200
        print("Request: path={path}, logged in={logged_in}".format(
            path=request.path, logged_in=page_status['logged_in']))
        if not page_status['logged_in'] or request.path == '/login.asp':
            return login_page
        if page_status['logged_in']:
            if request.path == '/MotoConnection.asp'.lower():
                return stats_page
        return ''

    def mb7621_login(request, context):
        print(dir(request))
        qs = urllib.parse.parse_qs(request.text)
        username = qs['loginUsername'][0]
        password_b64 = qs['loginPassword'][0]
        password = base64.b64decode(password_b64.encode('utf-8')).decode('utf-8')
        if (username, password) == page_status['auth']:
            page_status['logged_in'] = True
            context.headers = {
                "Location": "/MotoHome.asp",
                "Content-type": "text/html",
                "Connection": "close",
            }
        else:
            page_status['logged_in'] = False
            context.headers = {
                "Location": "/login.asp",
                "Content-type": "text/html",
                "Connection": "close",
            }
        # Always 302, regardless of authentication success.
        context.status_code = 302

    requests_mock.get('http://192.168.100.1/MotoConnection.asp', text=mb7621_request)
    requests_mock.get('http://192.168.100.1/MotoHome.asp', text=mb7621_request)
    requests_mock.get('http://192.168.100.1/login.asp', text=mb7621_request)
    requests_mock.post('http://192.168.100.1/goform/login', text=mb7621_login)
    collector = cable_modem_stats.MotorolaMB7621(auth=page_status['auth'])
    collector.run()
    downstream, upstream = collector.downstream_channels, collector.upstream_channels

    assert len(downstream) == 24
    downstream_actual = (
        # index, channel id, frequency, power, SNR, corrected, uncorrectables
        (0, 2, 789.0, 8.4, 39.8, 4, 0),
        (23, 40, 399.0, 4.3, 40.4, 91, 53),
    )
    for expected_channel in downstream_actual:
        idx = expected_channel[0]
        channel = downstream[idx]
        assert channel.channel_id == expected_channel[1]
        assert math.isclose(channel.frequency, expected_channel[2])
        assert math.isclose(channel.power, expected_channel[3])
        assert math.isclose(channel.snr, expected_channel[4])
        assert channel.corrected == expected_channel[5]
        assert channel.uncorrectables == expected_channel[6]

    assert len(upstream) == 4
    channel_1 = upstream[0]
    assert channel_1.channel_id == 3
    assert math.isclose(channel_1.frequency, 32.4)
    assert math.isclose(channel_1.power, 37.4)
    assert channel_1.snr is None
