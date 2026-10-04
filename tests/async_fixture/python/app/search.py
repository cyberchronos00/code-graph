import asyncio


async def fetch(q):
    await asyncio.sleep(0)
    return [q]


class SearchState:
    def __init__(self):
        self.results = []
        self.token = 0

    async def search(self, q):
        found = await fetch(q)
        self.results = found

    async def search_guarded(self, q):
        self.token += 1
        mine = self.token
        found = await fetch(q)
        if mine == self.token:
            self.results = found

    async def load_defaults(self):
        found = await fetch("defaults")
        self.results = found
