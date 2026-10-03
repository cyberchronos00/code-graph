import Fluent
import Vapor

final class Todo: Model, Content, @unchecked Sendable {
    static let schema = "todos"

    @ID(key: .id)
    var id: UUID?

    @Field(key: "title")
    var title: String

    init() {}

    init(id: UUID? = nil, title: String) {
        self.id = id
        self.title = title
    }
}

final class Tag: Model, @unchecked Sendable {
    static var schema: String { "tags" }

    @ID(key: .id)
    var id: UUID?
}
