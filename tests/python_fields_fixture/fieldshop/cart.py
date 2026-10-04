from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Item:
    name: str
    qty: int = 1
    kind: ClassVar[str] = "item"


class Cart:
    LIMIT = 10

    def __init__(self, owner: str):
        self.owner = owner
        self.items: list[Item] = []
        self.total = 0

    @property
    def size(self):
        return len(self.items)

    def add(self, it: Item):
        self.items.append(it)
        self.total += it.qty
        it.qty = it.qty + 1
        n = self.items.count(it)
        return n

    def save(self):
        pass


def checkout(c: Cart):
    c.total = 0
    print(c.owner)
    d = Cart("x")
    d.items.clear()
    del d.total
    d.save()
    return d.size


class Registry:
    def __init__(self):
        self.cache: dict = {}

    def put(self, k, v):
        self.cache[k] = v
        return self.cache[k]
