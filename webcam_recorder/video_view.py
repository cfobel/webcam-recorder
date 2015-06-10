import re
import platform
import time
from threading import Thread

from gi.repository import GObject, Gtk
if platform.system() == 'Linux':
    from gi.repository import GdkX11
from pygtk3_helpers.delegates import SlaveView
from pygtk3_helpers.file_chooser import FileChooserView
from path_helpers import path
from .pipeline import PipelineManager
from .caps import get_device_configs


GObject.threads_init()


class VideoModeSelector(SlaveView):
    def __init__(self, configs):
        if configs is None:
            self.configs = get_device_configs()
        else:
            self.configs = configs
        super(VideoModeSelector, self).__init__()

    def set_configs(self, configs):
        config_str_f = lambda c: '[{label}] {width}x{height}\t{framerate:.0f}fps'.format(**c)

        self.config_store.clear()
        for i, config_i in configs.iterrows():
            self.config_store.append([i, config_i, config_str_f(config_i)])

    def create_ui(self):
        self.config_store = Gtk.ListStore(int, object, str)
        self.set_configs(self.configs)

        self.config_combo = Gtk.ComboBox.new_with_model(self.config_store)
        renderer_text = Gtk.CellRendererText()
        self.config_combo.pack_start(renderer_text, True)
        self.config_combo.add_attribute(renderer_text, "text", 2)
        self.config_combo.connect("changed", self.on_config_combo_changed)
        self.widget.pack_start(self.config_combo, False, False, 0)

    def on_config_combo_changed(self, combo):
        tree_iter = combo.get_active_iter()
        if tree_iter is not None:
            model = combo.get_model()
            row_id, config = model[tree_iter][:2]
            self.on_config_selected(row_id, config)

    def on_config_selected(self, row_id, config):
        pass


class VideoArea(SlaveView):
    def __init__(self, width=None, height=None):
        self.xid = None

    def create_ui(self):
        self.drawingarea = Gtk.DrawingArea()
        self.drawingarea.set_size_request(480, 270)
        self.xid = self.drawingarea.get_property('window').get_xid()
        self.widget.pack_start(self.drawingarea, False, False, 0)

    def run(self):
        self.__run()
        self.window.show_all()
        # You need to get the XID after window.show_all().  You shouldn't get it
        # in the on_sync_message() handler because threading issues will cause
        # segfaults there.
        Gtk.main()

    def quit(self, window):
        Gtk.main_quit()


class VideoView(SlaveView):
    """
    SlaveView for displaying GStreamer video sink
    """

    def __init__(self, width=480, height=270):
        self.width = width
        self.height = height
        self.xid = None
        super(VideoView, self).__init__()

    def create_ui(self):
        self.drawingarea = Gtk.DrawingArea()
        self.drawingarea.set_size_request(self.width, self.height)
        self.drawingarea.connect('realize', self.on_realize)
        self.drawingarea.show()
        self.widget.pack_start(self.drawingarea, True, True, 0)

    def on_realize(self, widget):
        self.xid = self.drawingarea.get_property('window').get_xid()


class RecordView(SlaveView):
    def __init__(self, device_configs=None):
        self.video_view = None
        super(RecordView, self).__init__()
        if device_configs is None:
            self.device_configs = get_device_configs()
            self.device_configs = self.device_configs[(self.device_configs
                                                       .format == 'I420')
                                                      & (self.device_configs
                                                         .framerate > 10)]
        else:
            self.device_configs = device_configs
        self.pipeline_manager = PipelineManager()
        self.config_requested = None
        self.record_path = None

    def create_ui(self):
        self.record_control = RecordControl()
        self.video_view = VideoView()
        for slave in (self.record_control, self.video_view):
            slave.show()
            self.add_slave(slave)
        self.record_control.on_changed = self.on_options_changed

        # Pack load and save sections to end of row.
        self.widget.set_child_packing(self.record_control.widget, False, False,
                                      0, Gtk.PackType.START)

        def update_gui(view):
            while True:
                view.refresh_config()
                time.sleep(1.)

        self.pipeline_thread = Thread(target=update_gui, args=(self, ))
        self.pipeline_thread.daemon = True
        self.pipeline_thread.start()

    def on_options_changed(self, config, record_path):
        self.config_requested = config
        self.record_path = record_path

    def refresh_config(self):
        '''
        __NB__ This *must* be called from a *different* thread than the GUI/Gtk thread.
        '''
        if self.config_requested is not None:
            while self.video_view.xid is None:
                print 'waiting for GUI...'
                time.sleep(1)
            self.pipeline_manager.set_config(self.video_view.xid,
                                             self.config_requested,
                                             record_path=self.record_path)
            self.config_requested = None


class RecordControl(SlaveView):
    def __init__(self, device_configs=None):
        super(RecordControl, self).__init__()
        if device_configs is None:
            self.device_configs = get_device_configs()
            self.device_configs = self.device_configs[self.device_configs.format == 'I420']
        else:
            self.device_configs = device_configs
        self.config = None
        self._record_path = None

    def create_ui(self):
        self.mode_selector = VideoModeSelector(self.device_configs)
        self.widget.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.record_button = Gtk.CheckButton('Record')
        self.record_button.connect('toggled', self.on_record_toggled)
        self.record_button.show()

        self.auto_increment_button = Gtk.CheckButton('Auto-increment')
        self.auto_increment_button.show()

        filters = [{'name': 'MP4 (*.mp4)', 'pattern': ['*.mp4']},
                   {'name': 'AVI (*.avi)', 'pattern': ['*.avi']}]
        self.record_path_selector = FileChooserView(editable=True,
                                                    action=Gtk.FileChooserAction.SAVE,
                                                    title='Record to...',
                                                    filters=filters)
        self.record_path_selector.show()

        self.mode_selector.on_config_selected = self.on_config_selected
        self.mode_selector.show()
        self.add_slave(self.mode_selector)
        self.add_slave(self.record_path_selector)
        self.widget.pack_start(self.record_button, False, False, 0)
        self.widget.pack_start(self.auto_increment_button, False, False, 0)

        # Pack load and save sections to end of row.
        self.widget.set_child_packing(self.mode_selector.widget, False, False, 0,
                                      Gtk.PackType.START)

    @property
    def record_path(self):
        '''
        If recording is not enabled, return `None` as record path.
        '''
        if self.record_button.get_property('active') and (self.record_path_selector
                                                          .selected_path):
            return self.record_path_selector.selected_path
        else:
            return None

    @record_path.setter
    def record_path(self, value):
        '''
        If recording is not enabled, return `None` as record path.
        '''
        if value is not None:
            self.record_path_selector.selected_path = value

    def on_record_toggled(self, button):
        auto_increment = self.auto_increment_button.get_property('active')
        if auto_increment and self.record_path:
            record_path = path(self.record_path)
            namebase = record_path.namebase
            numeric_match = re.search(r'(?P<name>.*?)(?P<zero_pad>0*)'
                                      '(?P<count>\d+)$', namebase)
            if numeric_match:
                name = numeric_match.group('name')
                count = int(numeric_match.group('count')) + 1
            else:
                name = namebase
                count = 1
            namebase = '{name}{count}'.format(name=name, count=count)
            record_path = record_path.parent.joinpath(namebase +
                                                      record_path.ext)
            self.record_path = str(record_path)
        self.on_changed(self.config, self.record_path)

    def on_config_selected(self, row_id, config):
        self.config = config
        self.on_changed(self.config, self.record_path)

    def on_changed(self, config, record_path):
        pass
