class Editor:
    def __init__(self):
        self.text = ""
        self.is_applying_remote = False

    def on_text_changed(self):
        if self.is_applying_remote:
            return
        upload(self.text)

    def apply_remote(self, value):
        self.is_applying_remote = True
        self.text = value
        self.is_applying_remote = False

    async def refresh(self):
        value = await fetch_text()
        self.text = value


def upload(value):
    pass


async def fetch_text():
    return ""
