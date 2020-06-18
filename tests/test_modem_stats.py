import base64
import math
import os.path
import urllib.parse

import pytest
import requests

import cable_modem_stats


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
