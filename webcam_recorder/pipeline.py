import time
from threading import Thread

import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GstVideo
from path_helpers import path
from .caps import (get_video_source, get_caps_str, get_video_device_key,
                   get_bitrate)


class DrawPipeline(object):
    '''
    Draw video source to window with the specified `xid`.
    '''
    def run(self, xid, device_config=None):
        self.xid = xid
        # Create GStreamer pipeline
        self.pipeline = Gst.Pipeline()

        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)

        # This is needed to make the video output in our DrawingArea:
        self.bus.enable_sync_message_emission()
        self.bus.connect('sync-message::element', self.on_sync_message)

        # Create GStreamer elements
        if device_config is None:
            self.src = Gst.ElementFactory.make('autovideosrc', 'source')
        else:
            self.src = get_video_source()
            device_key = get_video_device_key()
            self.src.set_property(device_key, device_config['device'])
        self.filter_ = Gst.ElementFactory.make('capsfilter', 'filter')
        self.sink = Gst.ElementFactory.make('autovideosink', 'sink')
        self.sink.set_property('sync', False)
        caps = Gst.Caps(get_caps_str(device_config))
        self.filter_.set_property('caps', caps)

        # Add elements to the pipeline
        self.pipeline.add(self.src)
        self.pipeline.add(self.filter_)
        self.pipeline.add(self.sink)

        self.src.link(self.filter_)
        self.filter_.link(self.sink)
        self.pipeline.set_state(Gst.State.PLAYING)

    def on_sync_message(self, bus, msg):
        if msg.get_structure().get_name() == 'prepare-window-handle':
            print('prepare-window-handle')
            msg.src.set_property('force-aspect-ratio', True)
            msg.src.set_window_handle(self.xid)

    def on_error(self, bus, msg):
        print('on_error():', msg.parse_error())


class RecordPipeline(object):
    def run(self, xid, output_path, device_config=None, bitrate=350 << 3 << 10):
        '''
        Draw video source to window with the specified `xid` and record the
        video to the specified output file path.

        __NB__ The output file container is determined based on the extension
        of the output file path.  Supported containers are `avi` and `mp4`.  In
        either case, the video is encoded in MPEG4 format.

        Arguments
        ---------

         - `xid`: Integer identifier of window to draw frames to.
         - `output_path`: Output file path.
         - `device_config`:
           * Configuration dictionary or a `pandas.Series` in the format of a
             row of a frame returned by `caps.get_device_configs()`.
           * If not provided, the GStreamer `autovideosrc` is used.
         - `bitrate`: Target encode bit rate in bits/second (default=350kB/s)
        '''
        self.xid = xid
        # Create GStreamer pipeline
        self.pipeline = Gst.Pipeline()

        # Create bus to get events from GStreamer pipeline
        self.bus = self.pipeline.get_bus()
        self.bus.add_signal_watch()
        self.bus.connect('message::error', self.on_error)

        # This is needed to make the video output in our DrawingArea:
        self.bus.enable_sync_message_emission()
        self.bus.connect('sync-message::element', self.on_sync_message)

        # Create GStreamer elements
        if device_config is None:
            self.src = Gst.ElementFactory.make('autovideosrc', 'source')
        else:
            self.src = get_video_source()
            device_key = get_video_device_key()
            self.src.set_property(device_key, device_config['device'])
        self.sink = Gst.ElementFactory.make('autovideosink', 'sink')
        self.sink.set_property('sync', False)

        self.filter_ = Gst.ElementFactory.make('capsfilter', 'filter')
        tee = Gst.ElementFactory.make('tee', None)

        sink_queue = Gst.ElementFactory.make('queue', None)
        capture_queue = Gst.ElementFactory.make('queue', None)
        encoder = Gst.ElementFactory.make('avenc_mpeg4', None)
        encoder.set_property('bitrate', bitrate)
        encoder.set_property('bitrate-tolerance', 500 << 10)
        if path(output_path).ext.lower() == '.mp4':
            muxer = Gst.ElementFactory.make('mp4mux', None)
        elif path(output_path).ext.lower() == '.avi':
            muxer = Gst.ElementFactory.make('avimux', None)
        else:
            raise ValueError('Unsupported output file type: %s' %
                             path(output_path).ext)
        filesink = Gst.ElementFactory.make('filesink', None)
        filesink.set_property('location', output_path)

        videorate = Gst.ElementFactory.make('videorate', None)
        filter1 = Gst.ElementFactory.make('capsfilter', None)
        filter1.set_property('caps',
                             Gst.Caps('video/x-raw,framerate={framerate_numerator}/{framerate_denominator}'
                                      .format(**device_config)))

        caps = Gst.Caps(get_caps_str(device_config))
        self.filter_.set_property('caps', caps)

        src_elements = (self.src, self.filter_, videorate, filter1, tee)
        sink_elements = (sink_queue, self.sink)
        capture_elements = (capture_queue, encoder, muxer, filesink)

        # Add elements to the pipeline
        for d in src_elements + sink_elements + capture_elements:
            self.pipeline.add(d)

        for elements in (src_elements, sink_elements, capture_elements):
            for i, j in zip(elements[:-1], elements[1:]):
                i.link(j)

        tee.link(sink_elements[0])
        tee.link(capture_elements[0])


        self.output_path = output_path
        self.tee = tee
        self.muxer = muxer
        self.src_elements = src_elements
        self.sink_elements = sink_elements
        self.capture_elements = capture_elements

        self.pipeline.set_state(Gst.State.PLAYING)
        self._alive = True

    def on_sync_message(self, bus, msg):
        if msg.get_structure().get_name() == 'prepare-window-handle':
            print('prepare-window-handle')
            msg.src.set_property('force-aspect-ratio', True)
            msg.src.set_window_handle(self.xid)

    def on_error(self, bus, msg):
        print('on_error():', msg.parse_error())

    def eos_callback(self, pad, event):
        self.muxer.get_static_pad('src').remove_probe(self.eos_probe)
        self._alive = False
        return True

    def block_callback(self, pad, event):
        mux_pad = self.muxer.get_static_pad('src')
        capture_pad = self.capture_elements[0].get_static_pad('sink')
        self.eos_probe = mux_pad.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM,
                                           self.eos_callback)
        capture_pad.send_event(Gst.Event.new_eos())
        self.tee.pads[2].remove_probe(self.block_probe)
        return True

    def stop(self):
        # Start callback chain to send EOS (end of stream) event to video
        # muxer.  This is required, for example, when recording to `mp4`, where
        # the EOS event triggers the muxer to write the video header to the
        # file.  See [here][1] for more information.
        #
        # The basic idea is to:
        #
        #  - Block the tee source pad for the capture branch
        #  - Send EOS event through the capture queue `sink` pad
        #  - Wait until EOS is received by the muxer `sink` pad
        #  - Stop the pipeline (wait is complete when `self._alive` is `True`)
        #
        # [1]: http://gstreamer.freedesktop.org/data/doc/gstreamer/head/manual/html/section-dynamic-pipelines.html#section-dynamic-changing
        self.block_probe = self.tee.pads[2].add_probe(Gst.PadProbeType.BLOCK_DOWNSTREAM,
                                                      self.block_callback)
        for i in range(10):
            if not self._alive:
                self.pipeline.set_state(Gst.State.NULL)
                break
            time.sleep(.2)


class PipelineManager(object):
    def __init__(self):
        self.pipeline = None
        self.active_config = None

    def set_config(self, xid, device_config, record_path=None):
        print get_caps_str(device_config)

        self._stop()
        self.active_config = device_config  # = configs.iloc[config_index]

        kwargs = {'device_config': device_config}
        if record_path is not None:
            self.pipeline = RecordPipeline()
            kwargs['output_path'] = record_path
            kwargs['bitrate'] = get_bitrate(device_config.height)
        else:
            self.pipeline = DrawPipeline()

        gst_thread = Thread(target=self.pipeline.run, args=(xid, ), kwargs=kwargs)
        gst_thread.daemon = True
        gst_thread.start()
        gst_thread.join()

    def _stop(self):
        if self.pipeline is not None and hasattr(self.pipeline, 'pipeline'):
            if hasattr(self.pipeline, 'stop'):
                self.pipeline.stop()
            else:
                self.pipeline.pipeline.set_state(Gst.State.NULL)
            time.sleep(0.05)

    def __del__(self):
        self._stop()
