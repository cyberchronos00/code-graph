import Foundation

final class Editor {
    var text = ""
    var isApplyingRemote = false

    func textDidChange() {
        if isApplyingRemote { return }
        upload(text)
    }

    func applyRemote(_ value: String) {
        isApplyingRemote = true
        text = value
        isApplyingRemote = false
    }

    func refresh() {
        Task {
            let value = await fetchText()
            self.text = value
        }
    }

    func upload(_ value: String) {}
    func fetchText() async -> String { "" }
}
