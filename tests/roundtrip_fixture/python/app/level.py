def squash(v):
    return v / 2


def tame(v):
    """Keep v in the safe band. @cg-lossy"""
    return v


class LevelStore:
    def __init__(self):
        self.level = 0.0
        self.speed = 0.0
        self.gain = 0.0

    def save(self, v: float) -> None:
        self.level = round(v)
        self.speed = squash(v)
        self.gain = tame(v)


class Picker:
    def __init__(self, store: LevelStore):
        self.store = store
        self.value = store.level
        self.s = store.speed
        self.g = store.gain
