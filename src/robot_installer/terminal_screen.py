"""VT screen state and input encoding, independent of SSH and Qt rendering."""

import pyte


class TerminalBuffer:
    def __init__(self, columns=80, lines=24, reply=lambda *_: None):
        self.normal = pyte.HistoryScreen(columns, lines, history=3000)
        self.alternate = pyte.Screen(columns, lines)
        self.active = self.normal
        for screen in (self.normal, self.alternate):
            screen.write_process_input = reply
        self.stream = pyte.ByteStream(self)

    def __getattr__(self, name):
        value = getattr(self.active, name)
        if callable(value):
            # Stream binds event handlers once; resolve the current screen at dispatch time.
            return lambda *args, **kwargs: getattr(self.active, name)(*args, **kwargs)
        return value

    def set_mode(self, *modes, **kwargs):
        if kwargs.get("private"):
            if any(mode in (47, 1047, 1049) for mode in modes) and self.active is self.normal:
                self.alternate.reset()
                self.active = self.alternate
            if 1048 in modes:
                self.active.save_cursor()
        self.active.set_mode(*modes, **kwargs)

    def reset_mode(self, *modes, **kwargs):
        self.active.reset_mode(*modes, **kwargs)
        if kwargs.get("private"):
            if any(mode in (47, 1047, 1049) for mode in modes):
                self.active = self.normal
            if 1048 in modes:
                self.active.restore_cursor()

    def reset(self):
        self.normal.reset()
        self.alternate.reset()
        self.active = self.normal

    def resize(self, columns, lines):
        for screen in (self.normal, self.alternate):
            screen.resize(columns=max(2, columns), lines=max(2, lines))

    def feed(self, data):
        self.stream.feed(data)

    def rows(self):
        history = list(self.normal.history.top) if self.active is self.normal else []
        return history + [self.active.buffer[y] for y in range(self.active.lines)]

    def paste(self, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x1b", "")
        data = text.encode("utf-8")
        if 2004 << 5 in self.active.mode:
            return b"\x1b[200~" + data + b"\x1b[201~"
        return data.replace(b"\n", b"\r")
