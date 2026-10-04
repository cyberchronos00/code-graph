import Foundation

final class Thumbnails {
    var cache: [String: String] = [:]
    var scale = 2

    func thumbnail(url: String, size: Int) -> String {
        let rendered = render(url, size * scale)
        cache[url] = rendered
        return rendered
    }

    func badge(url: String, size: Int) -> String {
        let key = "\(url)-\(size)"
        let rendered = render(url, size)
        cache[key] = rendered
        return rendered
    }

    func store(url: String, image: String) {
        cache[url] = image
    }

    func render(_ url: String, _ size: Int) -> String { url + String(size) }
}
