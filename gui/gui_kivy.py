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
from .event_listener_sse import SSEEventListener
from .rpiplayer_http import RpiPlayerHttp

from src.events import EventType

import time

from kivy.properties import (
    NumericProperty,
    ObjectProperty,
    ListProperty,
    StringProperty,
    BooleanProperty,
    DictProperty,
)

rpiplayer = None
event_listener = None

logger = logging.getLogger(__name__)


class RpiMainScreen(BoxLayout):
    pass
    # def __init__(self, **kwargs):
    #     super().__init__(**kwargs)

    # def update_track_list(self):
    #     tracks_to_request = 100
    #     while True:
    #         tracks = rpiplayer.list(0, 100)
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
        event_listener.subscribe_all(
            {
                EventType.TrackChanged: self.on_change_track,
                EventType.Progress: self.on_current_progress,
                EventType.Playing: self.on_playing,
                EventType.Paused: self.on_paused,
                EventType.Stopped: self.on_stopped,
            }
        )

    @mainthread
    def on_play(self):
        if self.playing_state == PLAYING:
            rpiplayer.pause(True)
        elif self.playing_state == PAUSED:
            rpiplayer.pause(False)
        elif self.playing_state == STOPPED:
            rpiplayer.play()

    @mainthread
    def on_change_track(self, track_info):
        self.title = track_info["title"]
        self.performer = track_info["performer"]["name"]
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
        rpiplayer.set_pos(rel_pos)
        return True


class RpiPlayQueue(TabbedPanelItem):
    playing_state = NumericProperty(STOPPED)
    current_track_id = NumericProperty(0)

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
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
        self.track_index = 0

        self.update_list()

    @mainthread
    def update_list(self):
        tracks = rpiplayer.list(0, 1000)
        self.ids.rv.data = [self.track_to_data(track) for track in tracks]
        if len(self.ids.rv.data) > 0:
            self.ids.rv.data[self.current_track_id]["selected"] = self.playing_state

    def track_to_data(self, track):
        logger.warn("Track to data: " + str(track))
        return {
            "on_item_clicked": self.on_item_clicked,
            "index": track["index"],
            "title": track["title"],
            "performer": track["performer"]["name"],
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
        rpiplayer.play(index)

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
        event_listener.subscribe(EventType.TrackChanged, self.on_change_track)
        self.register_for_motion_event("touch")
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
            rpiplayer.next()
        elif touch.x - self.sensitivity > self.initial:
            rpiplayer.prev()

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
        self.paths = []
        self.browse("browse")

    def on_enter(self):
        self.paths = ["browse"]
        self.browse("/search/album/" + self.ids.input.text)

    def category_to_data(self, item, index):
        return {
            "on_item_clicked": self.on_item_clicked,
            "index": index,
            "title": item["name"],
            "performer": item["subname"],
            "image": item["image"],
            "selected": 0,
            "id": item["id"],
            "url": item["url"],
            "track": None,
        }

    def on_item_clicked(self, index):
        item = self.ids.rv.data[index]
        if len(item["url"]) != 0:
            path = "/browse" + item["url"]
            self.browse(path)
        else:
            rpiplayer.add([item["track"]])

    @mainthread
    def update_list(self, categories):
        start_index = 0
        print(self.paths)
        if len(self.paths) > 1:
            self.ids.rv.data = [
                {
                    "on_item_clicked": self.on_item_clicked,
                    "index": start_index,
                    "title": "...",
                    "performer": "Go back",
                    "image": {"small": "", "large": ""},
                    "selected": 0,
                    "id": "",
                    "url": "...",
                }
            ]
            start_index = 1
        else:
            self.ids.rv.data = []
        import json

        self.ids.rv.data.extend(
            [
                self.category_to_data(categories[i], i + start_index)
                for i in range(len(categories))
            ]
        )
        self.ids.rv.refresh_from_data()
        self.ids.rv.scroll_y = 1

    def browse(self, path):
        def task(callback):
            logger.warn("Path: %s", path)
            categories = rpiplayer.browse(path)
            callback(categories)

        if path == "...":
            self.paths.pop()
            path = self.paths[-1]
        else:
            self.paths.append(path)
        threading.Thread(target=task, args=(self.update_list,)).start()


class RpiMusicPlayer(App):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def build(self):
        return RpiMainScreen()


def run_app(rpiplayer_local: RpiPlayerHttp, event_listener_local: SSEEventListener):
    # Config.set("input", "mouse", "mouse,multitouch_on_demand")
    Config.set("graphics", "resizable", False)
    Config.set("graphics", "width", "800")
    Config.set("graphics", "height", "480")
    Config.set("kivy", "keyboard_mode", "systemandmulti")
    global rpiplayer
    rpiplayer = rpiplayer_local
    global event_listener
    event_listener = event_listener_local

    RpiMusicPlayer().run()
