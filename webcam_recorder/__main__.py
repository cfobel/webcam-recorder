import os
import pandas as pd
import numpy as np
import gi
gi.require_version('Gst', '1.0')
from gi.repository import GObject, Gst, Gtk, Gdk, GdkPixbuf, GstVideo
from .video_view import RecordView


if __name__ == '__main__':
    GObject.threads_init()
    Gst.init(None)

    view = RecordView()
    view.prepare_ui()
    view.widget.connect('destroy', lambda *args: view.hide_and_quit())
    view.show_and_run()
