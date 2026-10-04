from .search import fetch


class Detail:
    def __init__(self, seed):
        self.title = seed.strip()

    async def refresh(self, q):
        found = await fetch(q)
        self.title = found[0]
