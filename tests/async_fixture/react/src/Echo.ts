export class Editor {
  text = ''
  isApplyingRemote = false

  onTextChanged() {
    if (this.isApplyingRemote) return
    upload(this.text)
  }

  applyRemote(value: string) {
    this.isApplyingRemote = true
    this.text = value
    this.isApplyingRemote = false
  }

  async refresh() {
    const value = await fetchText()
    this.text = value
  }
}

function upload(value: string) {}
async function fetchText(): Promise<string> {
  return ''
}
