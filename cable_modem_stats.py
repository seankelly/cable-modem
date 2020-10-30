#!/usr/bin/env python3

import argparse
import base64
import inspect
import json
import math
import sys
from collections import namedtuple
from datetime import datetime

from bs4 import BeautifulSoup
import requests

try:
    import toml
except ImportError:
    toml = None


DownstreamChannel = namedtuple('DownstreamChannel',
                               'channel_id frequency power snr corrected uncorrectables')
UpstreamChannel = namedtuple('UpstreamChannel', 'channel_id frequency power snr')


def str_map(value, fn):
    if value:
        return fn(value)
    return value


def downstream(channel_details):
    channel_id = str_map(channel_details[0], int)
    frequency = str_map(channel_details[1], float)
    power = str_map(channel_details[2], float)
    snr = str_map(channel_details[3], float)
    corrected = str_map(channel_details[4], int)
    uncorrectables = str_map(channel_details[5], int)
    channel = DownstreamChannel(channel_id, frequency, power, snr, corrected, uncorrectables)
    return channel


def upstream(channel_details):
    channel_id = str_map(channel_details[0], int)
    frequency = str_map(channel_details[1], float)
    power = str_map(channel_details[2], float)
    snr = str_map(channel_details[3], float)
    channel = UpstreamChannel(channel_id, frequency, power, snr)
    return channel


class ArrisSB6183():
    """
    Connection statistics for Arris SB6183 cable modem.
    """

    STATUS_URL = 'http://192.168.100.1/RgConnect.asp'

    def __init__(self, output_format, modem_url=None):
        if modem_url:
            self.modem_url = modem_url
        else:
            self.modem_url = self.STATUS_URL
        self.output_format = output_format

    def parse_modem(self):
        try:
            resp = requests.get(self.modem_url)
            status_html = resp.content
            resp.close()
            soup = BeautifulSoup(status_html, 'html.parser')
        except Exception as exc:
            print('ERROR: Failed to get modem stats.  Aborting', file=sys.stderr)
            print(exc, file=sys.stderr)
            sys.exit(1)

        series = []
        current_time = math.trunc(datetime.now().timestamp())
        current_ns = '{}000000000'.format(current_time)

        # downstream table
        for table_row in soup.find_all("table")[2].find_all("tr")[2:]:
            if table_row.th:
                continue
            row_columns = table_row.find_all('td')
            channel = row_columns[0].text.strip()
            channel_id = row_columns[3].text.strip()
            frequency = row_columns[4].text.replace(" Hz", "").strip()
            power = row_columns[5].text.replace(" dBmV", "").strip()
            snr = row_columns[6].text.replace(" dB", "").strip()
            corrected = row_columns[7].text.strip()
            uncorrectables = row_columns[8].text.strip()

            downstream_result_dict = {
                'measurement': 'cable_modem',
                'time': current_ns,
                'fields': {
                    'channel_id': int(channel_id),
                    'frequency': int(frequency),
                    'power': float(power),
                    'snr': float(snr),
                    'corrected': int(corrected),
                    'uncorrectables': int(uncorrectables),
                },
                'tags': {
                    'channel': int(channel),
                    'direction': 'downstream',
                }
            }
            series.append(downstream_result_dict)

        # upstream table
        for table_row in soup.find_all("table")[3].find_all("tr")[2:]:
            if table_row.th:
                continue
            row_columns = table_row.find_all('td')
            channel = row_columns[0].text.strip()
            channel_id = row_columns[3].text.strip()
            frequency = row_columns[5].text.replace(" Hz", "").strip()
            power = row_columns[6].text.replace(" dBmV", "").strip()

            upstream_result_dict = {
                'measurement': 'cable_modem',
                'time': current_ns,
                'fields': {
                    'channel_id': int(channel_id),
                    'frequency': int(frequency),
                    'power': float(power),
                    'snr': float(snr),
                },
                'tags': {
                    'channel': int(channel),
                    'direction': 'upstream',
                }
            }
            series.append(upstream_result_dict)
        return series

    def output_modem_data(self, series):
        if self.output_format == 'influxdb':
            for point in series:
                tags = ['%s=%s' % (tag, value)
                        for tag, value in point['tags'].items()]
                fields = ['%s=%s' % (field, value)
                          for field, value in point['fields'].items()]
                line_protocol = '{measurement},{tags} {fields} {when}'.format(
                    measurement=point['measurement'], when=point['time'],
                    tags=','.join(tags), fields=','.join(fields))
                print(line_protocol)
        else:
            json.dump(series, sys.stdout)

    def run(self):
        modem_stats = self.parse_modem()
        self.output_modem_data(modem_stats)


class CableModem:

    FULL_NAME = None
    SHORT_NAME = None
    STATUS_URL = None
    AUTH_URL = None

    def __init__(self, auth=None, modem_url=None):
        if modem_url:
            self.modem_url = modem_url
        elif self.STATUS_URL:
            self.modem_url = self.STATUS_URL
        else:
            raise RuntimeError("No status URL given")
        self.auth = auth
        self.session = requests.Session()
        self.downstream_channels = []
        self.upstream_channels = []
        self.time = None

    def run(self):
        self._process_modem_status()

    def format_modem_data(self, output_format):
        if output_format == 'json':
            return self._format_json()
        elif output_format == 'influxdb':
            return self._format_influxdb()

    def _format_json(self):
        output_data = {
            'downstream': self.downstream_channels,
            'upstream': self.upstream_channels,
        }
        return json.dumps(output_data)

    def _format_influxdb(self):
        """Format all channels into InfluxDB line protocol."""

        def format_channel(measurement, current_ns, channel_data, **tags):
            """Format one channel into InfluxDB line protocol."""
            tags = ['{}={}'.format(tag, value)
                    for tag, value in tags.items()]
            fields = []
            for index, field_name in enumerate(channel_data._fields):
                value = channel_data[index]
                if value is not None:
                    fields.append('{}={}'.format(field_name, value))
            line_protocol = '{measurement},{tags} {fields} {when}'.format(
                measurement=measurement, when=current_ns,
                tags=','.join(tags), fields=','.join(fields))
            return line_protocol

        current_time = math.trunc(self.time.timestamp())
        current_ns = '{}000000000'.format(current_time)
        output_data = []
        for number, channel in enumerate(self.downstream_channels):
            output_data.append(
                format_channel('cable_modem', current_ns, channel,
                               direction='downstream', channel=number+1))
        for number, channel in enumerate(self.upstream_channels):
            output_data.append(
                format_channel('cable_modem', current_ns, channel,
                               direction='upstream', channel=number+1))
        return '\n'.join(output_data)

    def needs_authentication(self, page):
        return False

    def authenticate(self):
        pass

    def _process_modem_status(self):
        page = self._fetch_status_page()
        if self.needs_authentication(page):
            self.authenticate()
            page = self._fetch_status_page()
            if self.needs_authentication(page):
                raise RuntimeError("Failed to fetch status page: need authentication.")
        self._record_when()
        self._parse_status_page(page)

    def _record_when(self):
        self.time = datetime.now()

    def _fetch_status_page(self):
        try:
            resp = self.session.get(self.modem_url)
            status_html = resp.content
            resp.close()
            soup = BeautifulSoup(status_html, 'html.parser')
        except Exception as exc:
            print('ERROR: Failed to get modem stats.  Aborting', file=sys.stderr)
            print(exc, file=sys.stderr)
            sys.exit(1)
        return soup

    def _parse_status_page(self, page):
        tables = page.find_all("table")
        for table in tables:
            self._parse_table(table)

    def _parse_table(self, table_element, row_parser=None):
        table_data = []
        for table_row in table_element.find_all('tr', recursive=False):
            row_data = []
            for row_column in table_row.find_all('td', recursive=False):
                row_data.append(row_column.string)
            table_data.append(row_data)
        return table_data


class MotorolaMB7621(CableModem):
    """
    Connection statistics for Motorola MB7621 cable modem.
    """

    FULL_NAME = "Motorola MB7621"
    SHORT_NAME = "MB7621"
    STATUS_URL = 'http://192.168.100.1/MotoConnection.asp'
    AUTH_URL = 'http://192.168.100.1/goform/login'

    def needs_authentication(self, page):
        # Login page title:
        # <title>Motorola Cable Modem : Login</title>
        return 'Login' in page.title.string

    def authenticate(self):
        # loginUsername=NAME
        # loginPassword=BASE64_PASS
        if not self.auth:
            return
        username, password = self.auth
        password_bytes = password.encode('utf-8')
        login_auth = {
            'loginUsername': username,
            'loginPassword': base64.b64encode(password_bytes).decode('utf-8'),
        }
        self.session.post(self.AUTH_URL, data=login_auth)

    def _parse_status_page(self, page):
        tables = page.find_all('table', class_='moto-table-content')
        for table in tables:
            table_data = self._parse_table(table)
            if not table_data:
                continue
            header = table_data[0]
            header_name = header[0]
            if header_name != '\xa0\xa0\xa0Channel':
                continue
            # The downstream table has nine columns while upstream table has
            # seven columns. Use that to tell them apart but check the columns
            # have not been re-arranged.
            if len(header) == 9:
                column_headers = ["Channel ID", "Freq. (MHz)", "Pwr (dBmV)",
                                  "SNR (dB)", "Corrected", "Uncorrected"]
                if header[3:] != column_headers:
                    print("WARNING: Downstream table did not match.")
                    continue
                for row in table_data[1:]:
                    if row[1] != "Locked" or row[0] == "Total":
                        continue
                    channel_data = downstream(row[3:])
                    self.downstream_channels.append(channel_data)
            elif len(header) == 7:
                column_headers = ["Channel ID", "Symb. Rate (Ksym/sec)",
                                  "Freq. (MHz)", "Pwr (dBmV)"]
                if header[3:] != column_headers:
                    print("WARNING: Upstream table did not match.")
                    continue
                for row in table_data[1:]:
                    # This modem doesn't provide the SNR field and skip the
                    # symbol rate column.
                    if row[1] != "Locked":
                        continue
                    input_data = [row[3]] + row[5:] + [None]
                    channel_data = upstream(input_data)
                    self.upstream_channels.append(channel_data)


class ModemList:

    def __init__(self):
        self.modems = []
        self._build_list()

    def _build_list(self):
        members = inspect.getmembers(sys.modules[__name__])
        for name, obj in members:
            if hasattr(obj, 'FULL_NAME') and hasattr(obj, 'SHORT_NAME'):
                full_name = obj.FULL_NAME
                short_name = obj.SHORT_NAME
                if full_name and short_name:
                    self.modems.append((obj, full_name, short_name))

    def available_modems(self):
        output = ["Supported modems:"]
        for _, full_name, _short_name in self.modems:
            output.append("  %s" % full_name)
        return '\n'.join(output)

    def find_modem(self, modem):
        modem_map = {}
        for obj, full_name, short_name in self.modems:
            modem_map[full_name.lower()] = obj
            modem_map[short_name.lower()] = obj
        return modem_map.get(modem.lower())


def load_config(config_file_path):
    if not toml:
        print("toml library not available, cannot load config file")
        return
    with open(config_file_path, 'r') as config_file:
        config = toml.load(config_file)
        return config


def main():
    parser = argparse.ArgumentParser(description="A tool to scrape modem statistics")
    parser.add_argument('--list-modems', '-l', action='store_true',
                        help="List supported modems")
    parser.add_argument('--config', '-c', help="Config file for modem authentication")
    parser.add_argument('--url', help="Override URL to modem status page")
    parser.add_argument('--format', default='influxdb', choices=('influxdb', 'json'),
                        help='Output format, default of "influxdb"')
    parser.add_argument('modem', default=None, nargs='?',
                        help="Modem to collect statistics")
    args = parser.parse_args()

    modems = ModemList()
    if args.list_modems:
        print(modems.available_modems())
        return
    elif args.config:
        config = load_config(args.config) or {}

    modem = args.modem or config.get('modem')
    if modem is None:
        print("Must use either --list-modems or give modem")
        sys.exit(1)
    else:
        modem_class = modems.find_modem(modem)
        if not modem_class:
            print("Could not find modem \"%s\"" % modem)
            sys.exit(1)
        auth = None
        modem_url = args.url
        modem_options = config.get(modem_class.FULL_NAME)
        if modem_options:
            auth = (modem_options.get('username'), modem_options.get('password'))
            if not modem_url:
                modem_url = modem_options.get('url')
        collector = modem_class(auth=auth, modem_url=modem_url)
        collector.run()
        print(collector.format_modem_data(args.format))


if __name__ == '__main__':
    main()
