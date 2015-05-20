from matplotlib.backends.backend_gtk3cairo import FigureCanvasGTK3Cairo as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.gridspec import GridSpec
from matplotlib.backends.backend_gtk3 import NavigationToolbar2GTK3 as NavigationToolbar2
from matplotlib_helpers.points import PointsHandler
from pygtk3_helpers.file_chooser import FileChooserView
from pygtk3_helpers.delegates import SlaveView
from gi.repository import Gtk
from opencv_helpers import imshow, resize
import pandas as pd
import numpy as np
import cv


def load_im(input_file):
    import cv2
    from path_helpers import path

    input_file = path(input_file)
    if input_file.ext.lower() in ('.mp4', '.avi'):
        # Input file is a video, so grab a frame.
        video = cv2.VideoCapture(input_file)
        video.grab()
        success, mat = video.retrieve()
        if not success:
            raise IOError('Error grabbing frame from video.')
        im = cv2.cv.fromarray(mat)
        del video
    elif input_file.ext.lower() in ('.png', '.jpg'):
        # Input file is an image.
        im = cv2.cv.LoadImage(input_file)
    return im


class PointsIOView(SlaveView):
    def create_ui(self):
        self.widget.set_orientation(Gtk.Orientation.HORIZONTAL)
        self.reset_top = Gtk.Button('Reset top')
        self.reset_bottom = Gtk.Button('Reset bottom')
        self.reset_all = Gtk.Button('Reset points')

        for button in (self.reset_top, self.reset_bottom, self.reset_all):
            self.widget.pack_start(button, False, False, 0)

        filters = [{'name': 'Points (*.h5)', 'pattern': ['*.h5']}]
        self.load_points_selector = FileChooserView(label=False, filters=filters)
        self.load_points_selector.show()
        label = Gtk.Label('Load points')
        self.load_points_selector.box.pack_start(label, False, False, 0)
        self.load_points_selector.box.reorder_child(label, 0)

        self.save_points_selector = FileChooserView(label=False,
                                                    action=Gtk.FileChooserAction.SAVE,
                                                    title='Save points to...',
                                                    filters=filters)
        self.save_points_selector.show()
        label = Gtk.Label('Save points')
        self.save_points_selector.box.pack_start(label, False, False, 0)
        self.save_points_selector.box.reorder_child(label, 0)

        self.add_slave(self.save_points_selector)
        self.add_slave(self.load_points_selector)

        # Pack load and save sections to end of row.
        for widget in (self.save_points_selector.widget,
                       self.load_points_selector.widget):
            self.widget.set_child_packing(widget, False, False, 0, Gtk.PackType.END)


class VideoSelectorView(SlaveView):
    def create_ui(self):
        self.widget.set_size_request(640, 640)

        filters = [{'name': 'Video (*.mp4, *.avi)', 'pattern': ['*.mp4', '*.avi']}]
        self.video_selector = FileChooserView(editable=False, filters=filters)
        self.video_selector.show()
        label = Gtk.Label('Select video:')
        self.video_selector.box.pack_start(label, False, False, 0)
        self.video_selector.box.reorder_child(label, 0)
        self.video_selector.on_selected = self.on_selected
        self.add_slave(self.video_selector)
        self.widget.set_child_packing(self.video_selector.widget, False, False, 0,
                                      Gtk.PackType.START)

        self.io_view = PointsIOView()
        self.io_view.show()
        self.add_slave(self.io_view)
        self.io_view.load_points_selector.on_selected = self.on_points_load
        self.io_view.save_points_selector.on_selected = self.on_points_save
        self.io_view.reset_top.connect('clicked', lambda *args: self.reset(0))
        self.io_view.reset_bottom.connect('clicked', lambda *args: self.reset(1))
        self.io_view.reset_all.connect('clicked', lambda *args: self.reset())
        self.widget.set_child_packing(self.io_view.widget, False, False, 0,
                                      Gtk.PackType.START)

        self.registration_view = RegistrationView()
        self.registration_view.show()
        self.add_slave(self.registration_view)

        self.widget.show_all()

    def reset(self, index=None):
        '''
        Reset the points for the specified index position.  If no index is
        specified, reset points for all point handlers.
        '''
        points_handler_count = len(self.registration_view.points)
        if index is None:
            indexes = range(points_handler_count)
        else:
            indexes = [index]

        indexes = [i for i in indexes if i < points_handler_count]

        for i in indexes:
            self.registration_view.points[i].reset()
        if indexes:
            self.registration_view.update_transform()

    def on_points_load(self, value):
        if self.registration_view.axes:
            points = pd.read_hdf(value, '/points')
            self.registration_view.set_points(points)

    def on_points_save(self, value):
        if self.registration_view.axes:
            points = self.registration_view.get_points()
            points.to_hdf(value, '/points', format='t', data_columns=True)

    def on_selected(self, value):
        try:
            self.registration_view.set_image(value)
        except:
            pass


class RegistrationView(SlaveView):
    def __init__(self, *args, **kwargs):
        self.grid = kwargs.pop('grid', GridSpec(2, 1))
        super(RegistrationView, self).__init__(*args, **kwargs)
        self.fig = None
        self.axes = []
        self.im_in = None
        self.im_out = None
        self.points = []

    def create_ui(self):
        self.fig = Figure(figsize=(8, 8), dpi=100)
        self.axes = []

        sw = Gtk.ScrolledWindow()

        plot_box = Gtk.VBox()
        sw.add_with_viewport(plot_box)

        canvas = FigureCanvas(self.fig)
        canvas.set_size_request(500, 600)
        plot_box.pack_start(canvas, True, True, 0)

        toolbar = NavigationToolbar2(canvas, sw)
        self.widget.pack_start(toolbar, False, False, 0)
        self.widget.add(sw)

    def add_subplot(self, *args):
        axis = self.fig.add_subplot(*args)
        self.axes.append(axis)

    def plot(self, index, args, kwargs):
        self.axes[index].plot(*args, **kwargs)
        self.fig.canvas.draw()

    def clear(self):
        try:
            for widget in self.widget:
                self.widget.remove(widget)
        except (Exception, ), why:
            print why

    def refresh(self):
        self.fig.canvas.draw()

    def set_image(self, im, out_shape=None):
        import cv2

        if isinstance(im, str):
            im = load_im(im)
        elif isinstance(im, np.ndarray):
            im = cv2.cv.fromarray(im)
        self.fig.clf()
        self.axes = [self.fig.add_subplot(g) for g in self.grid]
        self.im_in = resize(im, im.width, im.height)
        if out_shape is None:
            out_shape = im.width, im.height
        self.im_out = resize(im, *out_shape)
        imshow(self.im_in, axis=self.axes[0], show_axis=True)
        imshow(self.im_out, axis=self.axes[1], show_axis=True)
        # Use `fig.set_tight_layout(True)` rather than `fig.tight_layout()` to
        # avoid [bug with GTK3 backend][1].
        #
        # [1]: https://github.com/matplotlib/matplotlib/issues/1852
        self.fig.set_tight_layout(True)
        self.points = [PointsHandler(ax) for ax in self.axes]

        # Update transformed image whenever the bounding box in either
        # the input or output frame has been changed.
        def do_update(*args):
            self.update_transform()

        for p in self.points:
            p.connect('box_release_event', do_update)
        self.update_transform()

    def update_transform(self):
        map_mat = cv.CreateMat(3, 3, cv.CV_32FC1)
        cv.GetPerspectiveTransform(map(tuple, self.points[0].points.values),
                                   map(tuple, self.points[1].points.values), map_mat)
        flags = cv.CV_WARP_FILL_OUTLIERS
        cv.WarpPerspective(self.im_in, self.im_out, map_mat, flags=flags)
        imshow(self.im_out, axis=self.axes[1], show_axis=True)
        self.refresh()

    def get_points(self):
        frames = []

        for i, p in enumerate(self.points):
            points_i = p.points.copy()
            points_i.insert(0, 'image_i', i)
            frames.append(points_i)
        return pd.concat(frames)

    def set_points(self, points):
        for i, df_i in points.groupby('image_i'):
            self.points[i].points = df_i[['x', 'y']]
        self.update_transform()

    def reset(self):
        for p in self.points:
            p.reset()
