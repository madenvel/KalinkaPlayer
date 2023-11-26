from enum import Enum
import logging
import threading
from kivy.app import App
from kivy.uix.widget import Widget

from kivy.clock import mainthread

from kivy.uix.tabbedpanel import TabbedPanelItem
from kivy.uix.recycleview import RecycleView
from kivy.uix.boxlayout import BoxLayout

from kivy.config import Config

from src.rpiasync import EventListener
from src.playqueue import EventType
from src.trackbrowser import TrackBrowser


from .playqueue import PlayQueue

import time

from kivy.properties import (
    NumericProperty,
    ObjectProperty,
    ListProperty,
    StringProperty,
    BooleanProperty,
    DictProperty,
)


class PlayQueueObj:
    pass


class EventListenerObj:
    pass


class TrackBrowserObj:
    pass


playqueueObj = PlayQueueObj()
event_listenerObj = EventListenerObj()
track_browserObj = TrackBrowserObj()


class RpiMainScreen(BoxLayout):
    pass
    # def __init__(self, **kwargs):
    #     super().__init__(**kwargs)

    # def update_track_list(self):
    #     tracks_to_request = 100
    #     while True:
    #         tracks = self.playqueue.list(0, 100)
    #         self.queue_data.extend(tracks)
    #         if len(tracks) < tracks_to_request:
    #             break

    # def update_selected_track(self, prev_index):
    #     self.queue_data[prev_index]["selected"] = False
    #     self.queue_data[self.track_index]["selected"] = True


PLAYING = 1
PAUSED = 2
STOPPED = 3


class RpiPlaybar(BoxLayout):
    playing_state = NumericProperty(STOPPED)
    title = StringProperty()
    performer = StringProperty()
    album_image = StringProperty("assets/transparent.png")
    duration = NumericProperty(0)
    current_time = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        event_listenerObj.event_listener.subscribe_all(
            {
                EventType.TrackChanged: self.on_change_track,
                EventType.Progress: self.on_current_progress,
                EventType.Playing: self.on_playing,
                EventType.Paused: self.on_paused,
                EventType.Stopped: self.on_stopped,
            }
        )

        self.playqueue = playqueueObj.playqueue

    @mainthread
    def on_play(self):
        if self.playing_state == PLAYING:
            self.playqueue.pause(True)
        elif self.playing_state == PAUSED:
            self.playqueue.pause(False)
        elif self.playing_state == STOPPED:
            self.playqueue.play()

    @mainthread
    def on_change_track(self, track_info):
        self.title = track_info["title"]
        self.performer = track_info["performer"]
        self.album_image = track_info["album"]["image"]["large"]
        self.duration = track_info["duration"]
        self.current_time = 0

    @mainthread
    def on_playing(self):
        self.playing_state = PLAYING

    @mainthread
    def on_paused(self):
        self.playing_state = PAUSED

    @mainthread
    def on_stopped(self):
        self.playing_state = STOPPED

    @mainthread
    def on_current_progress(self, current_time):
        self.current_time = current_time

    def format_time(self, duration_s):
        time_obj = time.gmtime(duration_s)
        return time.strftime("%M:%S", time_obj)

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        if not self.ids.progressbar.collide_point(*touch.pos):
            return False
        x_pos = self.ids.progressbar.to_widget(*touch.pos, relative=True)[0]
        rel_pos = x_pos / self.ids.progressbar.width
        self.playqueue.set_pos(rel_pos)
        return True


class RpiPlayQueue(TabbedPanelItem):
    playing_state = NumericProperty(STOPPED)
    current_track_id = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        event_listener = event_listenerObj.event_listener
        event_listener.subscribe_all(
            {
                EventType.TrackChanged: self.on_change_track,
                EventType.TracksAdded: self.on_track_added,
                EventType.TracksRemoved: self.on_track_removed,
                EventType.Playing: self.on_playing,
                EventType.Paused: self.on_paused,
                EventType.Stopped: self.on_stopped,
            }
        )
        self.playqueue = playqueueObj.playqueue
        self.track_index = 0

        self.update_list()

    @mainthread
    def update_list(self):
        tracks = self.playqueue.list(0, 1000)
        self.ids.rv.data = [self.track_to_data(track) for track in tracks]
        self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state

    def track_to_data(self, track):
        return {
            "on_item_clicked": self.on_item_clicked,
            "index": track["index"],
            "title": track["title"],
            "performer": track["performer"],
            "image": track["album"]["image"],
            "selected": 0,
        }

    @mainthread
    def on_change_track(self, track_info):
        self.ids.rv.data[self.current_track_id]["selected"] = 0
        self.current_track_id = track_info["index"]
        self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state
        self.ids.rv.refresh_from_data()

    @mainthread
    def on_track_added(self, tracks):
        for track in tracks:
            track["on_item_clicked"] = self.on_item_clicked
        self.ids.rv.data.extend([self.track_to_data(track) for track in tracks])
        self.ids.rv.refresh_from_data()

    @mainthread
    def on_track_removed(self, index):
        del self.ids.rv.data[index]
        self.ids.rv.refresh_from_data()

    def on_item_clicked(self, index):
        self.playqueue.play(index)

    @mainthread
    def on_playing(self):
        self.playing_state = PLAYING
        self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state
        self.ids.rv.refresh_from_data()

    @mainthread
    def on_paused(self):
        self.playing_state = PAUSED
        self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state
        self.ids.rv.refresh_from_data()

    @mainthread
    def on_stopped(self):
        self.playing_state = STOPPED
        self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state
        self.ids.rv.refresh_from_data()


class RpiPlayingNow(TabbedPanelItem):
    is_playing = BooleanProperty(False)
    album_image = StringProperty("assets/transparent.png")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        event_listenerObj.event_listener.subscribe(
            EventType.TrackChanged, self.on_change_track
        )
        self.register_for_motion_event("touch")
        self.playqueue = playqueueObj.playqueue
        self.initial = None
        self.sensitivity = 50

    def on_change_track(self, track_info):
        self.album_image = track_info["album"]["image"]["large"]

    def format_time(self, duration_s):
        time_obj = time.gmtime(duration_s)
        return time.strftime("%M:%S", time_obj)

    def on_touch_down(self, touch):
        super().on_touch_down(touch)
        if not self.ids.layout.collide_point(*touch.pos):
            return False
        self.initial = touch.x
        self.old_image_pos = (self.ids.image.x, self.ids.image.y)
        return True

    def on_touch_move(self, touch):
        super().on_touch_move(touch)
        if self.initial is None:
            return False
        if "pos" in touch.profile:
            dxi = self.initial - touch.x
            dx = (self.initial - touch.x) / self.sensitivity
            if abs(dx) > 1:
                dx /= abs(dx)
            self.ids.image.color = (1, 1, 1, 1 - abs(dx))
            self.ids.image.x = self.old_image_pos[0] - dxi
            if dx < 0:
                self.ids.prev_image.color = (1, 1, 1, abs(dx))
                self.ids.next_image.color = (1, 1, 1, 0)
            elif dx > 0:
                self.ids.next_image.color = (1, 1, 1, dx)
                self.ids.prev_image.color = (1, 1, 1, 0)
            else:
                self.ids.prev_image.color = (1, 1, 1, 0)
                self.ids.next_image.color = (1, 1, 1, 0)

    def on_touch_up(self, touch):
        super().on_touch_up(touch)
        if self.initial is None:
            return False
        if touch.x + self.sensitivity < self.initial:
            self.playqueue.next()
        elif touch.x - self.sensitivity > self.initial:
            self.playqueue.prev()

        self.ids.image.color = (1, 1, 1, 1)
        self.ids.prev_image.color = (1, 1, 1, 0)
        self.ids.next_image.color = (1, 1, 1, 0)
        self.ids.image.pos = self.old_image_pos
        self.old_image_pos = None
        self.initial = None

        return True


class RpiBrowser(TabbedPanelItem):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.track_browser: TrackBrowser = track_browserObj.track_browser
        self.playqueue = playqueueObj.playqueue
        self.current_path = []
        self.browse(self.current_path)

    def on_enter(self):
        self.browse(["search", "album", self.ids.input.text])

    def category_to_data(self, item, index):
        return {
            "on_item_clicked": self.on_item_clicked,
            "index": index,
            "title": item.name,
            "performer": "",  # item.info["album"]["artist"]["name"],
            "image": {"small": "", "large": ""},
            "selected": 0,
            "id": item.id,
            "path": item.path,
            "can_browse": item.can_browse,
            "track": item.info.get("track", None),
        }

    def on_item_clicked(self, index):
        item = self.ids.rv.data[index]
        if item["can_browse"] == True:
            path = item["path"]
            self.browse(path)
        else:
            self.playqueue.add([item["track"]])

    @mainthread
    def update_list(self, path, categories):
        start_index = 0
        if len(path) > 0:
            self.ids.rv.data = [
                {
                    "on_item_clicked": self.on_item_clicked,
                    "index": start_index,
                    "title": "...",
                    "performer": "",
                    "image": {"small": "", "large": ""},
                    "selected": 0,
                    "id": "",
                    "path": self.current_path,
                    "can_browse": True,
                }
            ]
            start_index = 1
        else:
            self.ids.rv.data = []
        self.ids.rv.data.extend(
            [
                self.category_to_data(categories[i], i)
                for i in range(start_index, len(categories))
            ]
        )
        self.current_path = path
        self.ids.rv.refresh_from_data()

    def browse(self, path):
        def task(callback):
            categories = self.track_browser.list_categories(path)
            callback(path, categories)

        threading.Thread(target=task, args=(self.update_list,)).start()


class RpiMusicPlayer(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def build(self):
        return RpiMainScreen()


def run_app(
    playqueue: PlayQueue, event_listener: EventListener, track_browser: TrackBrowser
):
    # Config.set("input", "mouse", "mouse,multitouch_on_demand")
    Config.set("graphics", "resizable", False)
    Config.set("graphics", "width", "800")
    Config.set("graphics", "height", "480")
    Config.set("kivy", "keyboard_mode", "systemandmulti")
    playqueueObj.playqueue = playqueue
    event_listenerObj.event_listener = event_listener
    track_browserObj.track_browser = track_browser
    RpiMusicPlayer().run()
