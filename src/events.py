from multiprocessing import Queue


class EventDispatcher:
    def __init__(self, event_queue: Queue):
        super().__init__()
        self.subscriptions = {}

    def dispatch(self, event_name, *args, **kwargs):
        if event_name in self.subscriptions:
            handlers = self.subscriptions[event_name]
            for handler in handlers:
                handler(*args, **kwargs)

    def subscribe(self, event_name, callback):
        self.subscriptions[event_name] = self.subscriptions.get(event_name, [])
        self.subscriptions[event_name].append(callback)
