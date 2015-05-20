# coding: utf-8
import platform
import re
from threading import Thread

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
import pandas as pd
import numpy as np
from path_helpers import path


class DeviceNotFound(Exception):
    pass


# Use recommended bitrates from YouTube:
#
#     2160p (4k)    35-45 Mbps
#     1440p (2k)    16 Mbps
#     1080p         8 Mbps
#     720p          5 Mbps
#     480p          2.5 Mbps
#     360p          1 Mbps
#
# [1]: https://support.google.com/youtube/answer/1722171?hl=en
BITRATES = (pd.Series([35, 16, 8, 5, 2.5, 1],
                      index=[2160, 1440, 1080, 720, 480, 360],
                      name='bits_per_second') * (1 << 20)).astype(int)
BITRATES.index.name = 'height'


def get_bitrate(height):
    return BITRATES[BITRATES.index >= height].iloc[-1]


def get_video_device_key():
    if platform.system() == 'Linux':
        device_key = 'device'
    elif platform.system() == 'Windows':
        device_key = 'device-name'
    else:
        raise ValueError('Unsupported platform: %s' % platform.system())
    return device_key


def get_video_sources():
    if platform.system() == 'Linux':
        try:
            devices = path('/dev/v4l/by-id').listdir()
        except OSError:
            raise DeviceNotFound, 'No devices available'
    else:
#         try:
#             devices = GstVideoSourceManager.get_video_source().probe_get_values_name(
#                     'device-name')
#         except:
#             devices = []
#         if not devices:
#             raise DeviceNotFound, 'No devices available'
#         device_key = 'device-name'
        raise ValueError('Unsupported platform: %s' % platform.system())
    return devices


def get_caps_str(device_config):
    return ('video/x-raw, format=(string){format}, width=(int){width}, '
            'height=(int){height}, framerate=(fraction){framerate_numerator}/'
            '{framerate_denominator}').format(**device_config)


def caps_str_to_df(caps_str):
    '''
    Parse caps string (as returned by `Gst.Pad.query_caps().to_string()`) into
    `pandas.DataFrame` table, with one row per configuration.
    '''
    structures = [dict([process_dict(v.groupdict())
                   for v in re.finditer(
                       r'(?P<key>[^ ]*)=\((?P<type>.*?)\)((?P<value>[^{].*?)|{(?P<values>.*?)})(,|$)', s)])
                  for s in caps_str.split(';')]

    df = pd.DataFrame(structures)
    df.reset_index(drop=True, inplace=True)

    def compute_multi(df):
        multi_values = [[(c, k) for k, v in df[c].iteritems()
                         if isinstance(v, list)] for c in df]
        value_lists = [m for m in multi_values if m]
        if value_lists:
            return pd.DataFrame(np.concatenate(value_lists),
                             columns=['label', 'index'])
        else:
            return pd.DataFrame()

    df_multi = compute_multi(df)

    while df_multi.shape[0] > 0:
        df = resolve_multi(df, df_multi)
        df_multi = compute_multi(df)

    if 'framerate' in df:
        df['framerate_numerator'] = df['framerate'].map(lambda v: v.num)
        df['framerate_denominator'] = df['framerate'].map(lambda v: v.denom)
        df['framerate'] = df['framerate_numerator'] / df['framerate_denominator']
    if 'pixel-aspect-ratio' in df:
        df['pixel-aspect-ratio_numerator'] = df['pixel-aspect-ratio'].map(lambda v: v.num)
        df['pixel-aspect-ratio_denominator'] = df['pixel-aspect-ratio'].map(lambda v: v.denom)
        df['pixel-aspect-ratio'] = (df['pixel-aspect-ratio_numerator'] /
                                    df['pixel-aspect-ratio_numerator'])

    return df.sort(['framerate', 'width', 'height']).reset_index(drop=True)


def resolve_multi(df, df_multi):
    for label, indexes in df_multi.groupby('label')['index']:
        values = np.concatenate(df[label].map(lambda v: [v]
                                              if not isinstance(v, list) else v))
        value_count = df[label].map(lambda v: len(v) if isinstance(v, list) else 1)
        del df[label]
        index = np.concatenate([np.repeat(v, n) for n, v in zip(value_count, df.index)])
        df2 = pd.DataFrame(values, index=index, columns=[label])
        break
    result = df2.join(df).reset_index(drop=True)
    if 'index' in result:
        result = result.drop('index', axis=1)
    return result


def translate(v, dtype):
    if dtype == 'fraction':
        return Gst.Fraction(*map(int, v.split('/')))
    elif dtype == 'int':
        return int(v)
    elif dtype == 'float':
        return int(v)
    elif dtype == 'string':
        return v.strip()
    else:
        raise TypeError('Unsupported type: %s' % dtype)


def process_dict(d):
    if d['values'] is not None:
        # A list of values was provided.
        value = map(lambda v: translate(v, dtype=d['type']),
                    d['values'].strip().split(','))
    else:
        value = translate(d['value'], dtype=d['type'])
    return d['key'], value


def get_video_source():
    if platform.system() == 'Linux':
        video_source = Gst.ElementFactory.make('v4l2src', 'video_source')
    else:
        video_source = Gst.ElementFactory.make('dshowvideosrc', 'video_source')
    return video_source


class VideoSourceCaps(object):
    def run(self, video_device=None):
        # Create GStreamer pipeline
        self.pipeline = Gst.Pipeline()

        self.errors = []
        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)

        # Create GStreamer elements
        if video_device is None:
            self.src = Gst.ElementFactory.make('autovideosrc', None)
        else:
            self.src = get_video_source()
            device_key = get_video_device_key()
            self.src.set_property(device_key, video_device)

        # Add elements to the pipeline
        self.pipeline.add(self.src)

        self.pipeline.set_state(Gst.State.READY)

        pad = self.src.get_static_pad('src')
        caps = pad.query_caps()
        scaps = caps.simplify()

        self.df_caps = caps_str_to_df(scaps.to_string())

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)

    def __del__(self):
        self.stop()
        del self.pipeline

    def on_error(self, bus, msg):
        self.errors.append(msg.parse_error())


def get_device_configs():
    '''
    Return a `pandas.DataFrame`, where each row corresponds to an available
    device configuration, including the `device` (i.e., the name of the
    device).
    '''
    frames = []

    for i in range(2):
        for device in get_video_sources():
            df_device_i = get_configs(device)
            df_device_i.insert(0, 'device', str(device))
            frames.append(df_device_i)

    device_configs = pd.concat(frames).drop_duplicates()
    device_configs['label'] = device_configs.device.map(
        lambda x: x.split('/')[-1].split('-')[1].split('_')[0])
    device_configs['bitrate'] = device_configs.height.map(get_bitrate)
    return device_configs


def get_configs(device):
    caps = VideoSourceCaps()
    caps.run(device)
    df_device_i = caps.df_caps.dropna().copy()
    return df_device_i
