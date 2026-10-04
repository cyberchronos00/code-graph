class Thumbnails:
    def __init__(self):
        self._cache = {}
        self.theme = "dark"

    def thumbnail(self, url, size):
        rendered = render(url, size, self.theme)
        self._cache[(url, size)] = rendered
        return rendered

    def badge(self, url, size):
        rendered = render(url, size, self.theme)
        self._cache[f"{url}-{size}-{self.theme}"] = rendered
        return rendered

    def store(self, url, image):
        self._cache[url] = image


def render(url, size, theme):
    return f"{url}{size}{theme}"
